from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.admin_role import AdminRolePermission, SUPER_ADMIN_SLUG
from app.models.user import RoleEnum, User

# Static catalog — the single source of truth for every permission key that
# exists in the system. Seeded into the `permissions` table by seed.py.
# Modules are limited to areas that actually have a real backend route today
# (no "courses.*" — no courses module exists; Programs covers that ground).
PERMISSION_CATALOG: list[dict] = [
    {"key": "students.view", "module": "Students", "action": "view", "description": "View student accounts."},
    {"key": "students.create", "module": "Students", "action": "create", "description": "Create student accounts."},
    {"key": "students.edit", "module": "Students", "action": "edit", "description": "Edit students, including enable/disable and password reset."},
    {"key": "students.delete", "module": "Students", "action": "delete", "description": "Delete student accounts."},
    {"key": "students.export", "module": "Students", "action": "export", "description": "Export student data."},

    {"key": "trainers.view", "module": "Trainers", "action": "view", "description": "View trainer accounts."},
    {"key": "trainers.create", "module": "Trainers", "action": "create", "description": "Create trainer accounts."},
    {"key": "trainers.edit", "module": "Trainers", "action": "edit", "description": "Edit trainers, including enable/disable and permissions."},
    {"key": "trainers.delete", "module": "Trainers", "action": "delete", "description": "Delete trainer accounts."},
    {"key": "trainers.reset_password", "module": "Trainers", "action": "reset_password", "description": "Reset a trainer's password."},

    {"key": "programs.view", "module": "Programs", "action": "view", "description": "View programs."},
    {"key": "programs.create", "module": "Programs", "action": "create", "description": "Create programs."},
    {"key": "programs.edit", "module": "Programs", "action": "edit", "description": "Edit programs."},
    {"key": "programs.delete", "module": "Programs", "action": "delete", "description": "Delete programs."},

    {"key": "batches.view", "module": "Batches", "action": "view", "description": "View batches and their class schedule."},
    {"key": "batches.create", "module": "Batches", "action": "create", "description": "Create batches."},
    {"key": "batches.edit", "module": "Batches", "action": "edit", "description": "Edit batches, including scheduling classes."},
    {"key": "batches.delete", "module": "Batches", "action": "delete", "description": "Delete batches."},

    {"key": "lab_access.view", "module": "Lab Access", "action": "view", "description": "View per-student lab slot bookings and lock/unlock status."},
    {"key": "lab_access.manage", "module": "Lab Access", "action": "manage", "description": "Manually lock or unlock a student's lab access."},

    {"key": "meetings.view", "module": "Meetings", "action": "view", "description": "View meetings, participants, chat, notes and recordings."},
    {"key": "meetings.create", "module": "Meetings", "action": "create", "description": "Schedule new meetings."},
    {"key": "meetings.edit", "module": "Meetings", "action": "edit", "description": "Edit meeting details and settings."},
    {"key": "meetings.manage", "module": "Meetings", "action": "manage", "description": "Start, end, and manage a live meeting (participants, recording)."},
    {"key": "meetings.delete", "module": "Meetings", "action": "delete", "description": "Cancel or delete meetings and recordings."},

    {"key": "placement.view", "module": "Placement", "action": "view", "description": "View placement interviews."},
    {"key": "placement.create", "module": "Placement", "action": "create", "description": "Schedule placement interviews."},
    {"key": "placement.edit", "module": "Placement", "action": "edit", "description": "Edit placement interviews."},
    {"key": "placement.delete", "module": "Placement", "action": "delete", "description": "Delete placement interviews."},

    {"key": "admin_users.view", "module": "Admin Users", "action": "view", "description": "View admin accounts."},
    {"key": "admin_users.create", "module": "Admin Users", "action": "create", "description": "Create admin accounts."},
    {"key": "admin_users.edit", "module": "Admin Users", "action": "edit", "description": "Edit admins, including enable/disable, role, and password reset."},
    {"key": "admin_users.delete", "module": "Admin Users", "action": "delete", "description": "Delete admin accounts."},

    {"key": "roles.view", "module": "Roles", "action": "view", "description": "View roles and permissions."},
    {"key": "roles.create", "module": "Roles", "action": "create", "description": "Create custom roles."},
    {"key": "roles.edit", "module": "Roles", "action": "edit", "description": "Edit role permission grants."},
    {"key": "roles.delete", "module": "Roles", "action": "delete", "description": "Delete custom roles."},

    {"key": "activity_logs.view", "module": "Activity Logs", "action": "view", "description": "View the audit trail."},
    {"key": "activity_logs.export", "module": "Activity Logs", "action": "export", "description": "Export activity logs as CSV."},

    {"key": "settings.view", "module": "Settings", "action": "view", "description": "View system settings."},
    {"key": "settings.edit", "module": "Settings", "action": "edit", "description": "Edit system settings."},

    {"key": "support.view", "module": "Support", "action": "view", "description": "View support tickets."},
    {"key": "support.reply", "module": "Support", "action": "reply", "description": "Reply to a student on a support ticket."},
    {"key": "support.assign", "module": "Support", "action": "assign", "description": "Assign a support ticket to an admin."},
    {"key": "support.change_status", "module": "Support", "action": "change_status", "description": "Change a support ticket's status, including reopening."},
    {"key": "support.change_priority", "module": "Support", "action": "change_priority", "description": "Change a support ticket's priority."},
    {"key": "support.add_internal_note", "module": "Support", "action": "add_internal_note", "description": "Add an internal note, hidden from the student, to a support ticket."},
    {"key": "support.close", "module": "Support", "action": "close", "description": "Close a support ticket."},
    {"key": "support.delete", "module": "Support", "action": "delete", "description": "Delete a support ticket."},
    {"key": "support.export", "module": "Support", "action": "export", "description": "Export support ticket data."},

    {"key": "leads.view", "module": "Leads", "action": "view", "description": "View the leads database."},
    {"key": "leads.create", "module": "Leads", "action": "create", "description": "Add new leads."},
    {"key": "leads.edit", "module": "Leads", "action": "edit", "description": "Edit lead details and status."},
    {"key": "leads.delete", "module": "Leads", "action": "delete", "description": "Delete leads."},
    {"key": "leads.export", "module": "Leads", "action": "export", "description": "Export lead data."},

    # Deliberately NOT in _OPERATIONAL_MODULES below — a plain Admin does
    # not get this by default, only a role explicitly granted it (e.g.
    # lab_vm_admin) or Super Admin (always computed as having everything).
    {"key": "lab_vm.view", "module": "VM Lab", "action": "view", "description": "View VM lab access status and history."},
    {"key": "lab_vm.grant", "module": "VM Lab", "action": "grant", "description": "Grant a student VM lab access."},
    {"key": "lab_vm.revoke", "module": "VM Lab", "action": "revoke", "description": "Revoke a student's VM lab access."},
    {"key": "lab_vm.manage_vms", "module": "VM Lab", "action": "manage_vms", "description": "Add, edit, or remove VMs from the lab inventory."},
    {"key": "lab_vm.view_history", "module": "VM Lab", "action": "view_history", "description": "View the full VM lab access audit history."},

    # Also deliberately NOT in _OPERATIONAL_MODULES — same reasoning as
    # lab_vm above. Community members themselves have no permissions at all
    # (role == community, not role == admin — see get_effective_permissions);
    # these keys only govern which ADMIN can see the Community Database.
    {"key": "community.view", "module": "Community", "action": "view", "description": "View the community members database."},
    {"key": "community.edit", "module": "Community", "action": "edit", "description": "Enable or disable a community member's account."},
    {"key": "community.export", "module": "Community", "action": "export", "description": "Export community member data."},
]

PERMISSION_KEYS: frozenset[str] = frozenset(p["key"] for p in PERMISSION_CATALOG)

_OPERATIONAL_MODULES = ["students", "trainers", "programs", "batches", "placement", "lab_access", "meetings", "leads"]

# Default grants for the three editable system roles. Super Admin is not
# here — its access is computed (see user_has_permission), never read from
# this table.
DEFAULT_GRANTS: dict[str, list[str]] = {
    "admin": [k for k in PERMISSION_KEYS if k.split(".")[0] in _OPERATIONAL_MODULES]
    + ["activity_logs.view"]
    + [k for k in PERMISSION_KEYS if k.split(".")[0] == "support"],
    # Support Admin gets enough to actually do support work — view, reply,
    # move a ticket through the status workflow — but not the
    # higher-trust actions (assign/close/delete/export/internal notes),
    # which stay Admin/Super-Admin-only.
    "support_admin": [f"{m}.view" for m in _OPERATIONAL_MODULES] + ["support.view", "support.reply", "support.change_status"],
    # Leads Database Admin: full run of the leads pipeline, plus read-only
    # visibility into the student and trainer databases (so a lead's
    # progress into an actual enrolled student/trainer can be cross-checked)
    # — nothing else. This role can only be assigned by a Super Admin (see
    # RESTRICTED_ROLE_SLUGS in routes_admin_users.py), not because its
    # permissions here are unusually dangerous, but so a plain Admin can't
    # mint themselves a side-channel into the leads pipeline via a role they
    # weren't given directly.
    "leads_admin": [k for k in PERMISSION_KEYS if k.split(".")[0] == "leads"] + ["students.view", "trainers.view"],
    # Lab VM Admin: full run of the VM lab access system (grant/revoke/
    # inventory/history), plus read-only visibility into the student
    # database (so a grant can be issued against a real, confirmed
    # student). Super-Admin-only to assign, same reasoning as leads_admin —
    # see RESTRICTED_ROLE_SLUGS in routes_admin_users.py.
    "lab_vm_admin": [k for k in PERMISSION_KEYS if k.split(".")[0] == "lab_vm"] + ["students.view"],
}


def get_effective_permissions(db: Session, user: User) -> list[str]:
    """Every permission key the user currently holds. Empty list for
    non-admin roles and for admins with no admin_role assigned yet."""
    if user.role != RoleEnum.admin or user.admin_role is None:
        return []
    if user.admin_role.slug == SUPER_ADMIN_SLUG:
        return sorted(PERMISSION_KEYS)
    rows = db.scalars(
        select(AdminRolePermission.permission_key).where(AdminRolePermission.admin_role_id == user.admin_role_id)
    ).all()
    return sorted(rows)


def user_has_permission(db: Session, user: User, permission_key: str) -> bool:
    if user.role != RoleEnum.admin or user.admin_role is None:
        return False
    if user.admin_role.slug == SUPER_ADMIN_SLUG:
        return True
    exists = db.scalar(
        select(AdminRolePermission.id).where(
            AdminRolePermission.admin_role_id == user.admin_role_id,
            AdminRolePermission.permission_key == permission_key,
        )
    )
    return exists is not None
