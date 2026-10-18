"""Permanent student deletion policy. Caller owns the transaction.

Shared conversations, support, batch/meeting content and security logs survive
with nullable identity links. No shared container or other user's content is
deleted. Unknown dependencies fail closed rather than cascading.
"""
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.database import Base
from app.models.file_cleanup import FileCleanupJob
from app.models.lab_vm import LabVm, LabVmAccess, LabVmAccessStatus
from app.models.meeting import MeetingParticipant, ParticipantStatus
from app.models.support import SupportAttachment, SupportMessage
from app.models.user import User
from app.core.lab_vm_secrets import encrypt_rdp_password, generate_windows_compliant_password
from app.core.upload_cleanup import cleanup_path
from app.crud.session import revoke_all_sessions

# Delete only rows exclusively owned by the account. FK columns, not table
# cascades, define ownership. Shared-table identity links are detached below.
OWNED = {
    "student_profiles": "user_id", "staff_profiles": "user_id",
    "admin_profiles": "user_id", "community_profiles": "user_id",
    "auth_sessions": "user_id", "user_google_identities": "user_id",
    "google_login_tickets": "user_id", "program_enrollments": "student_id",
    "batch_enrollments": "student_id", "batch_chat_read_state": "user_id",
    "interview_schedules": "student_id", "lab_access_overrides": "student_id",
    "lab_slot_bookings": "student_id", "lab_vm_access": "student_id",
    "community_post_email_deliveries": "user_id",
    "mfa_credentials": "user_id", "mfa_recovery_codes": "user_id",
    "mfa_challenges": "user_id",
}
DETACH = {
    "auth_audit_log": ("user_id",),
    "batch_messages": ("sender_id",),
    "conversations": ("user_a_id", "user_b_id"),
    "direct_messages": ("sender_id",),
    "support_tickets": ("requester_id", "assigned_to"),
    "support_messages": ("sender_id",),
    "meeting_participants": ("user_id",),
    "meeting_messages": ("user_id",),
    "meeting_notes": ("updated_by",),
    "lab_access_audit_log": ("student_id",),
    "lab_vm_access_audit_log": ("student_id", "performed_by"),
    "lab_access_overrides": ("updated_by",),
    "lab_vm_access": ("revoked_by",),
    "leads": ("created_by_id",),
    "system_settings": ("updated_by_id",),
    "batches": ("trainer_id",),
}


def stage_student_deletion(db: Session, user: User) -> dict[str, int]:
    """Stage a complete account removal, including a durable upload manifest.

    The route locks the user first. PostgreSQL FK checks serialize concurrent
    dependents against that lock; the final FK checks remain authoritative.
    Unexpected shared ownership (e.g. a student recorded as meeting host) is
    reported without moving ownership or erasing anyone else's records.
    """
    uid = user.id
    for table in Base.metadata.sorted_tables:
        for column in table.c:
            if not any(fk.target_fullname == "users.id" for fk in column.foreign_keys):
                continue
            if column.name == OWNED.get(table.name) or column.name in DETACH.get(table.name, ()):
                continue
            if db.scalar(select(column).where(column == uid).limit(1)) is not None:
                raise HTTPException(409, f"Deletion blocked by shared ownership in {table.name}.{column.name}. "
                                    "Reassign those records before retrying; "
                                    "no changes were made.")

    attachments = db.scalars(select(SupportAttachment).join(
        SupportMessage, SupportAttachment.message_id == SupportMessage.id
    ).where(SupportMessage.sender_id == uid)).all()
    references = {("support", a.storage_reference) for a in attachments}
    if user.photo_path:
        references.add(("profile_photo", user.photo_path))
    cleanup_jobs = 0
    # Do not unlink a reference also used by another account/message.
    for kind, reference in references:
        if kind == "support":
            shared = db.scalar(select(SupportAttachment.id).join(
                SupportMessage, SupportAttachment.message_id == SupportMessage.id
            ).where(SupportAttachment.storage_reference == reference,
                    (SupportMessage.sender_id != uid) | SupportMessage.sender_id.is_(None)).limit(1))
        else:
            shared = db.scalar(select(User.id).where(User.id != uid, User.photo_path == reference).limit(1))
        if shared is None:
            try:
                cleanup_path(kind, reference)  # validate containment BEFORE committing
            except (ValueError, OSError):
                raise HTTPException(409, "A student upload has an invalid storage reference. "
                                    "Repair the reference before deletion; no changes were made.")
            db.add(FileCleanupJob(storage_kind=kind, storage_reference=reference))
            cleanup_jobs += 1
    for attachment in attachments:
        db.delete(attachment)
    db.flush()

    # Retain machine inventory; invalidate the outgoing grant's saved RDP
    # password. The Windows agent applies the change on its next heartbeat.
    vm_ids = db.scalars(select(LabVmAccess.vm_id).where(
        LabVmAccess.student_id == uid, LabVmAccess.status == LabVmAccessStatus.active
    )).all()
    for vm in db.scalars(select(LabVm).where(LabVm.id.in_(vm_ids)).order_by(LabVm.id).with_for_update()).all():
        encrypted = encrypt_rdp_password(generate_windows_compliant_password())
        vm.current_agent_password_encrypted = encrypted
        # A VM uses a shared Windows account. Keep remaining live grantees'
        # snapshots consistent with the rotated password, preserving access.
        db.execute(update(LabVmAccess).where(
            LabVmAccess.vm_id == vm.id, LabVmAccess.student_id != uid,
            LabVmAccess.status == LabVmAccessStatus.active,
        ).values(rdp_password_encrypted=encrypted))

    revoke_all_sessions(db, uid)
    db.execute(update(MeetingParticipant).where(
        MeetingParticipant.user_id == uid,
        MeetingParticipant.status.in_([
            ParticipantStatus.INVITED, ParticipantStatus.WAITING,
            ParticipantStatus.ADMITTED, ParticipantStatus.JOINED,
        ]),
    ).values(status=ParticipantStatus.REMOVED, left_at=datetime.now(timezone.utc)))
    counts = {}
    for table_name, columns in DETACH.items():
        table = Base.metadata.tables[table_name]
        for name in columns:
            result = db.execute(update(table).where(table.c[name] == uid).values({name: None}))
            counts[f"{table_name}.{name}.detached"] = result.rowcount
    for table_name, name in OWNED.items():
        table = Base.metadata.tables[table_name]
        result = db.execute(delete(table).where(table.c[name] == uid))
        counts[f"{table_name}.deleted"] = result.rowcount
    counts["support_attachments.deleted"] = len(attachments)
    counts["file_cleanup_jobs.queued"] = cleanup_jobs
    # Core delete avoids stale ORM cascades after explicit dependency handling.
    db.execute(delete(User.__table__).where(User.id == uid))
    return counts
