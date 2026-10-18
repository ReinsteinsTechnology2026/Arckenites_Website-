"""Proposed conservative reset policy. Pure classification, never database DML.

Rows contain only primary keys, roles, FK values, and limited metadata. This is
a preview, not authorization. Ambiguous ownership is retained for manual review.
"""
from collections import Counter
from app.models.admin_role import SUPER_ADMIN_SLUG

PRESERVE = {
    "admin_roles", "permissions", "admin_role_permissions", "programs",
    "system_settings", "alembic_version", "lab_vms", "file_cleanup_jobs",
}
AUDIT = {"auth_audit_log", "lab_access_audit_log", "lab_vm_access_audit_log"}
OWNED = {
    "student_profiles": "user_id", "staff_profiles": "user_id", "admin_profiles": "user_id",
    "community_profiles": "user_id", "auth_sessions": "user_id", "user_google_identities": "user_id",
    "google_login_tickets": "user_id", "program_enrollments": "student_id",
    "batch_enrollments": "student_id", "interview_schedules": "student_id",
    "lab_slot_bookings": "student_id", "lab_access_overrides": "student_id",
    "lab_vm_access": "student_id", "batch_chat_read_state": "user_id",
    "community_post_email_deliveries": "user_id",
    "mfa_credentials": "user_id", "mfa_recovery_codes": "user_id",
    "mfa_challenges": "user_id",
}
MANUAL = {"batches", "class_sessions", "study_materials", "class_videos", "leads"}
ALL_TABLES = PRESERVE | AUDIT | set(OWNED) | MANUAL | {
    "users", "community_registrations", "contact_enquiries", "community_posts",
    "conversations", "direct_messages", "batch_messages", "support_tickets",
    "support_messages", "support_attachments", "meetings", "meeting_participants",
    "meeting_messages", "meeting_notes", "meeting_recordings",
}


def preview(rows, foreign_keys):
    """Build deterministic per-row *proposals*, with explicit blockers.

    Mandatory config and audit rows are never proposed for deletion. Super Admin
    users are protected; other admins and ordinary student/staff/community
    accounts with consistent roles and profiles are candidates. A retained parent pulls its
    dependent content into the retained set. Unknown identities are held.
    """
    actions = {table: {} for table in rows}
    blockers = []
    role_slugs = {r["id"]: r.get("slug") for r in rows.get("admin_roles", [])}
    users = {r["id"]: r for r in rows.get("users", [])}
    super_ids = {uid for uid, r in users.items()
                 if r.get("role") == "admin" and role_slugs.get(r.get("admin_role_id")) == SUPER_ADMIN_SLUG}
    admin_ids = {uid for uid, r in users.items() if r.get("role") == "admin"}
    protected = set(super_ids)
    candidates = set()
    admin_profile_ids = {r.get("user_id") for r in rows.get("admin_profiles", [])}
    profile_roles = {"student_profiles": "student", "staff_profiles": "staff", "community_profiles": "community"}
    mismatched = set(admin_profile_ids) - admin_ids
    for table, role in profile_roles.items():
        mismatched.update(r.get("user_id") for r in rows.get(table, [])
                          if users.get(r.get("user_id"), {}).get("role") != role)
    for uid, row in users.items():
        if uid in protected:
            continue
        if row.get("role") == "admin" and role_slugs.get(row.get("admin_role_id")) not in (None, SUPER_ADMIN_SLUG) and uid in admin_profile_ids and uid not in mismatched:
            candidates.add(uid)
        elif row.get("role") in ("student", "staff", "community") and row.get("admin_role_id") is None and uid not in mismatched:
            candidates.add(uid)
        else:
            blockers.append(f"Review account role/profile classification: user ID {uid}")
    if not super_ids:
        blockers.append("No current Super Admin account was identified")
    if not any(users[uid].get("is_active") for uid in super_ids):
        blockers.append("No active Super Admin identified; do not change passwords or active status to bypass this")
    if rows.get("file_cleanup_jobs"):
        blockers.append("Pending upload cleanup queue needs a hold/resume decision before any worker restart")
    if any(r.get("status") == "active" for r in rows.get("lab_vm_access", [])):
        blockers.append("Active VM grants need a separately approved rotation/enforcement plan")
    if any(r.get("status") == "LIVE" for r in rows.get("meetings", [])):
        blockers.append("Live meetings need an external media-session plan")

    def set_action(table, row, action):
        actions[table][row["id"]] = action

    def all_candidate(ids):
        return bool(ids) and all(uid in candidates for uid in ids)

    for table in sorted(rows):
        for row in rows[table]:
            if table in PRESERVE or table in AUDIT:
                action = "retain"
            elif table not in ALL_TABLES or table in MANUAL:
                action = "manual"
            elif table == "users":
                action = "retain" if row["id"] in protected else ("candidate_delete" if row["id"] in candidates else "manual")
            elif table in OWNED:
                action = "candidate_delete" if row.get(OWNED[table]) in candidates else "retain"
            elif table in ("community_registrations", "contact_enquiries"):
                action = "candidate_delete"
            elif table == "conversations":
                action = "candidate_delete" if all_candidate([row.get("user_a_id"), row.get("user_b_id")]) else "retain"
            elif table == "direct_messages":
                action = "candidate_delete" if row.get("sender_id") in candidates else "retain"
            elif table == "batch_messages":
                action = "candidate_delete" if row.get("sender_id") in candidates else "retain"
            elif table == "support_tickets":
                action = "candidate_delete" if row.get("requester_id") in candidates and (
                    row.get("assigned_to") is None or row.get("assigned_to") in candidates) else "retain"
            elif table == "support_messages":
                action = "candidate_delete" if row.get("sender_id") in candidates else "retain"
            elif table == "support_attachments":
                action = "candidate_delete"  # Parent propagation below keeps shared content.
            elif table == "community_posts":
                action = "candidate_delete" if row.get("created_by_id") in candidates else "retain"
            elif table == "meetings":
                action = "candidate_delete" if row.get("host_id") in candidates and row.get("batch_id") is None else "retain"
            elif table == "meeting_participants":
                action = "candidate_delete" if row.get("user_id") in candidates else "retain"
            elif table == "meeting_messages":
                action = "candidate_delete" if row.get("user_id") in candidates else "retain"
            elif table == "meeting_notes":
                action = "candidate_delete" if row.get("updated_by") in candidates else "retain"
            elif table == "meeting_recordings":
                action = "manual"  # No recording owner/creator; do not infer ownership from host.
            else:
                action = "manual"
            set_action(table, row, action)

    # Retaining a shared container retains all of its history. Conversely,
    # protected/unknown contributions prevent deletion of their containers.
    parent_links = [
        ("batch_messages", "batch_id", "batches"),
        ("direct_messages", "conversation_id", "conversations"),
        ("support_messages", "ticket_id", "support_tickets"),
        ("support_attachments", "message_id", "support_messages"),
        ("meeting_participants", "meeting_id", "meetings"),
        ("meeting_messages", "meeting_id", "meetings"),
        ("meeting_notes", "meeting_id", "meetings"),
        ("meeting_recordings", "meeting_id", "meetings"),
        ("community_post_email_deliveries", "post_id", "community_posts"),
    ]
    # Email deliveries are associations, not contributions. Retained posts do
    # not force delivery rows for removed users to remain.
    for _ in range(12):
        changed = False
        for child, column, parent in parent_links:
            for row in rows.get(child, []):
                current = actions[child][row["id"]]
                parent_action = actions.get(parent, {}).get(row.get(column))
                if parent_action is None:
                    blockers.append(f"Missing projected parent: {child}.{column}")
                    continue
                if child == "community_post_email_deliveries":
                    if current != "candidate_delete" and parent_action == "candidate_delete":
                        actions[parent][row[column]] = "retain"
                        changed = True
                    continue
                if current != "candidate_delete" and parent_action == "candidate_delete":
                    actions[parent][row[column]] = "retain"
                    changed = True
                if parent_action != "candidate_delete" and current == "candidate_delete":
                    actions[child][row["id"]] = "retain"
                    changed = True
        if not changed:
            break

    detach = []
    # Only explicitly reviewed nullable FK links may be detached. Never
    # modify configuration attribution simply to enable deleting an account.
    from app.core.student_deletion import DETACH
    permitted_detach = {(table, column) for table, columns in DETACH.items() for column in columns}
    permitted_detach.discard(("system_settings", "updated_by_id"))
    for fk in foreign_keys:
        if (fk["target_table"] != "users" or fk.get("target_schema", "public") != "public"
                or fk.get("source_schema", "public") != "public"):
            continue
        table, column = fk["source_table"], fk["source_column"]
        for row in rows.get(table, []):
            if actions[table][row["id"]] == "candidate_delete" or row.get(column) not in candidates:
                continue
            if (table, column) in permitted_detach and fk["nullable"]:
                detach.append({"table": table, "id": row["id"], "column": column})
            else:
                blockers.append(f"Retained/manual {table}.{column} references candidate user ID {row[column]}")

    for table in sorted(rows):
        manual = sum(a == "manual" for a in actions[table].values())
        if manual:
            blockers.append(f"Manual row-level ownership/retention approval needed: {table} ({manual} rows)")
    summary = {}
    for table in sorted(actions):
        counts = Counter(actions[table].values())
        summary[table] = {"total": len(actions[table]), "candidate_delete": counts["candidate_delete"],
                          "retain": counts["retain"], "manual": counts["manual"],
                          "detach_links": sum(d["table"] == table for d in detach)}
    return {
        "policy": "draft-protect-super-admin-clear-other-accounts-preserve-shared-history-v2",
        "execution_enabled": False,
        "protected_admin_ids": sorted(protected), "super_admin_ids": sorted(super_ids),
        "protected_super_admin_count": len(super_ids),
        "admin_ids_requiring_individual_review": sorted(admin_ids - protected - candidates),
        "candidate_user_ids": sorted(candidates), "counts": summary,
        "proposed_actions": actions, "proposed_detach_links": sorted(detach, key=lambda d: (d["table"], d["id"], d["column"])),
        "blockers": sorted(set(blockers)),
        "required_gates": ["current production schema inventory", "row-level policy approval", "verified DB/uploads backups and isolated restore",
                           "write freeze and integrations/VM/media plan", "signed target-bound approval", "separately reviewed executor"],
    }
