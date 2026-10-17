import io
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from openpyxl import Workbook
from sqlalchemy import delete, func, or_, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.core.deps import require_permission, require_role
from app.core.approved_programs import APPROVED_PROGRAM_NAMES, require_approved_program, resolve_approved_catalog
from app.core.security import hash_password, validate_password_strength, verify_password
from app.core.student_credentials import send_student_credentials
from app.crud.audit import write_audit_event
from app.crud.permissions import user_has_permission
from app.crud.session import revoke_all_sessions
from app.crud.system_settings import get_settings
from app.crud.user import generate_student_code
from app.database import get_db
from app.models.audit_log import AuthAuditLog, AuthEventType
from app.models.batch import BatchEnrollment
from app.models.batch_chat import BatchChatReadState, BatchMessage
from app.models.chat import Conversation, DirectMessage
from app.models.community import CommunityProfile
from app.models.google_identity import UserGoogleIdentity
from app.models.interview_schedule import InterviewSchedule
from app.models.lab_access_audit import LabAccessAuditLog
from app.models.lab_access_override import LabAccessOverride
from app.models.lab_slot_booking import LabSlotBooking
from app.models.lab_vm import LabVmAccess, LabVmAccessAuditLog
from app.models.meeting import MeetingMessage, MeetingParticipant
from app.models.program import EnrollmentStatusEnum, Program, ProgramEnrollment
from app.models.session import AuthSession
from app.models.student import PROGRAM_LABELS, ProgramEnum, StudentProfile
from app.models.support import SupportMessage, SupportTicket
from app.models.user import RoleEnum, User
from app.schemas.admin_students import (
    CreateStudentRequest,
    CredentialEmailOut,
    DeleteStudentResponse,
    ResendStudentCredentialsRequest,
    StudentAdminOut,
    StudentCreatedOut,
    StudentProgramOption,
    StudentProgramOptionsOut,
    UpdateStudentRequest,
)

# Every table (besides auth_sessions/auth_audit_log, which are always safe to
# clear/detach — see delete_student) that can hold a row pointing at a
# student's users.id. If ANY of these has a row for the student, the account
# has real history and must be deactivated, never hard-deleted or
# cascade-deleted. Keep this in sync with ForeignKey("users.id") across
# app/models/* — the "Student has related records" 409 that used to reach
# admins came from this exact set of tables being incomplete, so a delete hit
# an unhandled foreign-key constraint and the whole commit was rolled back.
_STUDENT_RELATED_RECORD_CHECKS: list[tuple[type, str]] = [
    (BatchEnrollment, "student_id"),
    (ProgramEnrollment, "student_id"),
    (InterviewSchedule, "student_id"),
    (LabAccessOverride, "student_id"),
    (LabAccessAuditLog, "student_id"),
    (LabSlotBooking, "student_id"),
    (LabVmAccess, "student_id"),
    (LabVmAccessAuditLog, "student_id"),
    (BatchMessage, "sender_id"),
    (BatchChatReadState, "user_id"),
    (DirectMessage, "sender_id"),
    (SupportTicket, "requester_id"),
    (SupportMessage, "sender_id"),
    (MeetingParticipant, "user_id"),
    (MeetingMessage, "user_id"),
    (CommunityProfile, "user_id"),
    (UserGoogleIdentity, "user_id"),
]

router = APIRouter(prefix="/admin", tags=["admin-students"])
logger = logging.getLogger("students")


def _email_in_use(db: Session, email: str) -> bool:
    # Include old students whose generated login is different from their
    # known contact email. Never guess or rename existing accounts.
    return (
        db.scalar(select(User.id).where(func.lower(func.trim(User.username)) == email).limit(1)) is not None
        or db.scalar(select(StudentProfile.id).where(func.lower(func.trim(StudentProfile.email)) == email).limit(1)) is not None
    )


_DUPLICATE_EMAIL = "A student account with this email already exists, or the email is already used as another portal login."


@router.get("/students/program-options", response_model=list[StudentProgramOption] | StudentProgramOptionsOut)
def student_program_options(
    include_status: bool = False,
    db: Session = Depends(get_db),
    _actor: User = Depends(require_role("admin")),
):
    # Provisioning does not require unrelated programs.view permission.
    if not any(user_has_permission(db, _actor, key) for key in (
        "students.create", "students.edit", "programs.view", "batches.view", "batches.create", "batches.edit",
    )):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    catalog = resolve_approved_catalog(db)
    options = catalog.options()
    if not include_status:
        return options  # Retain the existing list response for current clients.
    return StudentProgramOptionsOut(
        options=options, approved_names=list(APPROVED_PROGRAM_NAMES),
        missing_programs=catalog.missing, ambiguous_programs=catalog.ambiguous,
        inactive_programs=catalog.inactive, detail=catalog.notice,
    )


def _client_meta(request: Request) -> tuple[str, str]:
    ip = request.client.host if request.client else "unknown"
    ua = request.headers.get("user-agent", "unknown")
    return ip, ua


def _get_student_or_404(db: Session, student_id: int) -> User:
    user = db.get(User, student_id)
    if user is None or user.role != RoleEnum.student:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Student not found")
    return user


def _student_has_related_records(db: Session, user_id: int) -> bool:
    """True if this student has any business/history record anywhere in the
    schema (enrollments, support tickets, lab access, batch/direct chat,
    meetings, community profile, linked Google identity, ...). Checked with
    cheap EXISTS-style `LIMIT 1` lookups, not a count, since we only need a
    yes/no answer."""
    for model, column_name in _STUDENT_RELATED_RECORD_CHECKS:
        column = getattr(model, column_name)
        if db.scalar(select(column).where(column == user_id).limit(1)) is not None:
            return True
    convo_exists = db.scalar(
        select(Conversation.id)
        .where(or_(Conversation.user_a_id == user_id, Conversation.user_b_id == user_id))
        .limit(1)
    )
    return convo_exists is not None


@router.post("/students", response_model=StudentCreatedOut, status_code=201)
def create_student(
    payload: CreateStudentRequest,
    request: Request,
    db: Session = Depends(get_db),
    actor: User = Depends(require_permission("students.create")),
):
    if get_settings(db).require_strong_passwords:
        error = validate_password_strength(payload.temp_password)
        if error:
            raise HTTPException(status_code=400, detail=error)
    try:
        # The existing code allocator scans codes. Serialize this short
        # transaction across workers to avoid assigning the same code.
        if db.get_bind().dialect.name == "postgresql":
            db.execute(text("SELECT pg_advisory_xact_lock(19041701)"))
        if _email_in_use(db, payload.email):
            raise HTTPException(status_code=409, detail=_DUPLICATE_EMAIL)
        program = require_approved_program(db, payload.program_id)
        user = User(
            username=payload.email, password_hash=hash_password(payload.temp_password),
            full_name=payload.full_name, role=RoleEnum.student,
            is_active=True, must_change_password=True,
        )
        db.add(user)
        db.flush()
        # Retain the enum used by existing program-specific dashboards
        # when this catalogue slug has a legacy counterpart.
        legacy_program = ProgramEnum._value2member_map_.get(program.slug)
        db.add(StudentProfile(user_id=user.id, email=payload.email, program=legacy_program, program_id=program.id,
                              student_code=generate_student_code(db)))
        db.add(ProgramEnrollment(program_id=program.id, student_id=user.id, status=EnrollmentStatusEnum.active))
        ip, ua = _client_meta(request)
        write_audit_event(
            db, AuthEventType.student_created, ip, ua, user=actor,
            detail=f"Created student {payload.email}", module="students", target=payload.email,
            status="success", commit=False,
        )
        program_name = program.name
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except IntegrityError as exc:
        db.rollback()
        if _email_in_use(db, payload.email):
            raise HTTPException(status_code=409, detail=_DUPLICATE_EMAIL)
        logger.error("Student creation constraint failure error_type=%s", type(exc).__name__)
        raise HTTPException(status_code=409, detail="Unable to create student. Please refresh and try again.")
    except Exception as exc:
        db.rollback()
        # Do not log SQL parameters or return database internals.
        logger.error("Student creation failed error_type=%s", type(exc).__name__)
        raise HTTPException(status_code=500, detail="Unable to create student. Please try again.")

    db.refresh(user)
    student = StudentAdminOut.model_validate(user)
    # Email only after the user, profile, enrollment and audit are durable.
    delivery = send_student_credentials(
        student_id=user.id, full_name=user.full_name, email=payload.email,
        temp_password=payload.temp_password, program_name=program_name,
    )
    return StudentCreatedOut(**student.model_dump(), **delivery.model_dump())


@router.post("/students/{student_id}/credentials-email", response_model=CredentialEmailOut)
def resend_student_credentials(
    student_id: int, payload: ResendStudentCredentialsRequest,
    db: Session = Depends(get_db),
    _actor: User = Depends(require_permission("students.create")),
):
    user = _get_student_or_404(db, student_id)
    if not user.is_active or not user.must_change_password:
        raise HTTPException(status_code=400, detail="Credentials can only be resent for an active student awaiting a password change.")
    if not user.email or user.username != user.email.strip().lower():
        raise HTTPException(status_code=400, detail="This account uses a legacy login. Share its existing login ID securely instead.")
    if not verify_password(payload.temp_password, user.password_hash):
        raise HTTPException(status_code=400, detail="The temporary password does not match. Enter the original temporary password.")
    result = send_student_credentials(
        student_id=user.id, full_name=user.full_name, email=user.username,
        temp_password=payload.temp_password,
        program_name=user.program_name or PROGRAM_LABELS.get(user.student_profile.program, "Not assigned"),
    )
    return CredentialEmailOut(
        credential_email_sent=result.credential_email_sent,
        detail=(f"Credentials were emailed to {user.username}." if result.credential_email_sent else
                "The credential email could not be sent. The student account and password are unchanged; please retry later."),
    )


@router.get("/students", response_model=list[StudentAdminOut])
def list_students(
    db: Session = Depends(get_db),
    _actor: User = Depends(require_permission("students.view")),
):
    users = db.scalars(
        select(User)
        .where(User.role == RoleEnum.student)
        .options(selectinload(User.student_profile), selectinload(User.program_enrollments).selectinload(ProgramEnrollment.program))
        .order_by(User.created_at.desc())
    ).all()
    return [StudentAdminOut.model_validate(u) for u in users]


@router.get("/students/export")
def export_students(
    db: Session = Depends(get_db),
    _actor: User = Depends(require_permission("students.export")),
):
    """On-demand snapshot for the admin to keep outside the app — the
    database (not this file) remains the source of truth for login/auth,
    this is generated fresh from it on every request."""
    users = db.scalars(
        select(User)
        .where(User.role == RoleEnum.student)
        .options(selectinload(User.student_profile), selectinload(User.program_enrollments).selectinload(ProgramEnrollment.program))
        .order_by(User.created_at.desc())
    ).all()

    wb = Workbook()
    ws = wb.active
    ws.title = "Students"
    headers = [
        "Username", "Full Name", "Mobile Number", "Email", "Current Role",
        "Program", "Status", "Awaiting First Login", "Created At",
    ]
    ws.append(headers)

    for u in users:
        profile = u.student_profile
        program_label = u.program_name or (PROGRAM_LABELS.get(profile.program, "") if profile and profile.program else "")
        current_role = profile.current_role.value if profile and profile.current_role else ""
        ws.append([
            u.username,
            u.full_name,
            profile.phone if profile else "",
            (profile.email if profile else None) or (u.username if "@" in u.username else ""),
            current_role,
            program_label,
            "Active" if u.is_active else "Inactive",
            "Yes" if u.must_change_password else "No",
            u.created_at.strftime("%Y-%m-%d %H:%M") if u.created_at else "",
        ])
        # User-supplied names/contact data must remain literal Excel text,
        # even when a value begins with '='.
        for cell in ws[ws.max_row]:
            if cell.data_type == "f":
                cell.data_type = "s"

    for col in ws.columns:
        width = max(len(str(cell.value)) for cell in col if cell.value is not None) if col else 10
        ws.column_dimensions[col[0].column_letter].width = min(max(width + 2, 12), 40)

    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)

    filename = f"arckenites-students-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M')}.xlsx"
    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.patch("/students/{student_id}", response_model=StudentAdminOut)
def update_student(
    student_id: int,
    payload: UpdateStudentRequest,
    request: Request,
    db: Session = Depends(get_db),
    actor: User = Depends(require_permission("students.edit")),
):
    user = _get_student_or_404(db, student_id)
    ip, ua = _client_meta(request)

    if payload.full_name is not None:
        user.full_name = payload.full_name
    if payload.program_id is not None or payload.program is not None:
        program_query = (Program.id == payload.program_id if payload.program_id is not None
                         else Program.slug == payload.program)
        assigned = db.scalar(select(Program).where(program_query))
        profile = user.student_profile
        if assigned is None:
            raise HTTPException(status_code=400, detail="Please select an existing approved program.")
        # Old clients may echo a historical assignment during a name/status
        # edit. Treat that as unchanged, without creating enrollment rows.
        unchanged = (assigned.id == user.program_id or (
            profile.program_id is None and profile.program is not None and assigned.slug == profile.program.value
        ))
        if not unchanged:
            assigned = require_approved_program(db, assigned.id)
            profile.program_id = assigned.id
            profile.program = ProgramEnum._value2member_map_.get(assigned.slug)
            enrollment = db.scalar(select(ProgramEnrollment).where(
                ProgramEnrollment.student_id == user.id, ProgramEnrollment.program_id == assigned.id,
            ))
            if enrollment is None:
                db.add(ProgramEnrollment(student_id=user.id, program_id=assigned.id, status=EnrollmentStatusEnum.active))
            elif enrollment.status in (EnrollmentStatusEnum.withdrawn, EnrollmentStatusEnum.rejected, EnrollmentStatusEnum.pending):
                enrollment.status = EnrollmentStatusEnum.active
    if payload.is_active is not None:
        if payload.is_active != user.is_active:
            write_audit_event(
                db, AuthEventType.student_enabled if payload.is_active else AuthEventType.student_disabled,
                ip, ua, user=actor, detail=f"{'Enabled' if payload.is_active else 'Disabled'} student {user.username}",
                module="students", target=user.username, status="success", commit=False,
            )
        user.is_active = payload.is_active
        if payload.is_active is False:
            # Same as disabling a trainer (routes_admin_staff.update_staff) —
            # deactivating must also kill any session already in flight, or a
            # student logged in before the toggle keeps working until that
            # token naturally expires.
            revoke_all_sessions(db, user.id)
    if payload.reset_temp_password is not None:
        user.password_hash = hash_password(payload.reset_temp_password)
        user.must_change_password = True
        user.failed_login_count = 0
        user.locked_until = None
        write_audit_event(
            db, AuthEventType.student_password_reset, ip, ua, user=actor,
            detail=f"Reset password for student {user.username}", module="students", target=user.username,
            status="success", commit=False,
        )

    if payload.full_name is not None or payload.program is not None or payload.program_id is not None:
        write_audit_event(
            db, AuthEventType.student_updated, ip, ua, user=actor, detail=f"Edited student {user.username}",
            module="students", target=user.username, status="success", commit=False,
        )

    db.add(user)
    try:
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.error("Student update failed error_type=%s", type(exc).__name__)
        raise HTTPException(status_code=500, detail="Unable to update student. Please try again.")
    db.refresh(user)

    return StudentAdminOut.model_validate(user)


@router.delete("/students/{student_id}", response_model=DeleteStudentResponse)
def delete_student(
    student_id: int,
    request: Request,
    db: Session = Depends(get_db),
    actor: User = Depends(require_permission("students.delete")),
):
    """Hard-deletes a student ONLY if they have zero related records anywhere
    in the schema. Otherwise deactivates the account instead (reusing the
    same `is_active` mechanism PATCH /admin/students/{id} already uses) —
    never cascade-deletes a student's enrollments, support tickets, lab
    access, chat history, or similar business/history records, and never
    silently drops the foreign keys that protect them.

    This used to always attempt a hard delete, detaching/clearing an
    incomplete list of related tables before `db.delete(user)`. Any table
    outside that list (support tickets, batch chat, lab VM access, meetings,
    linked Google identity, ...) still pointed at the account, so the commit
    hit an unhandled foreign-key violation and the whole transaction rolled
    back, surfacing only the generic "Contact engineering" 409. This
    endpoint is idempotent: calling it again on an already-deactivated
    student just reconfirms deactivation rather than erroring."""
    user = _get_student_or_404(db, student_id)
    username = user.username
    ip, ua = _client_meta(request)

    if _student_has_related_records(db, user.id):
        was_active = user.is_active
        user.is_active = False
        revoke_all_sessions(db, user.id)
        db.add(user)
        if was_active:
            # commit=False: the audit entry is staged alongside is_active and
            # the session-revocation update, NOT committed here. All three
            # are committed together in the single db.commit() below, so a
            # failure anywhere rolls back everything — including is_active
            # and the session revocation, which a separate, earlier commit
            # here would otherwise have already made durable and
            # unrollbackable. (This exact gap — write_audit_event's own
            # independent commit silently flushing this branch's pending
            # is_active change before the "real" commit — was a confirmed
            # bug, caught only by a real PostgreSQL integration test; a
            # mocked Session's commit() never actually flushes, so unit
            # tests alone couldn't see it.)
            write_audit_event(
                db, AuthEventType.student_disabled, ip, ua, user=actor,
                detail=f"Deactivated student {username} instead of deleting (related records exist)",
                module="students", target=username, status="success",
                commit=False,
            )
        try:
            db.commit()
        except Exception:
            # Single commit covers is_active, the session revocation, and
            # the audit entry together — this rollback now genuinely
            # reverts all three, not just whichever happened to not already
            # be committed by something else.
            db.rollback()
            raise
        db.refresh(user)
        return DeleteStudentResponse(
            action="deactivated",
            detail=(
                "This student has related records (enrollments, support tickets, lab access, "
                "chat history, or similar) so the account was deactivated instead of permanently "
                "deleted. The student can no longer log in, and all of their active sessions were "
                "revoked."
            ),
            student=StudentAdminOut.model_validate(user),
        )

    # No related records anywhere we check — genuinely safe to hard-delete.
    # Audit history is detached (not deleted) so security records survive;
    # auth_audit_log.user_id is nullable for exactly this reason. Sessions
    # are deleted outright since they have no standalone value once gone.
    db.execute(update(AuthAuditLog).where(AuthAuditLog.user_id == user.id).values(user_id=None))
    db.execute(delete(AuthSession).where(AuthSession.user_id == user.id))

    db.delete(user)  # cascades to student_profile via the User.student_profile relationship
    try:
        db.commit()
    except IntegrityError:
        # Safety net only — _student_has_related_records should already have
        # caught any blocking row above. If something we don't check for yet
        # still points at this account, fail safely instead of a bare 500 or
        # a partially-applied delete, without leaking the SQL/constraint name.
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Student has related records that prevent deletion. No changes were made — "
                "please retry; the account will be deactivated instead of deleted."
            ),
        )

    write_audit_event(
        db, AuthEventType.student_deleted, ip, ua, user=actor, detail=f"Deleted student {username}",
        module="students", target=username, status="success",
    )
    return DeleteStudentResponse(
        action="deleted",
        detail=f"Student {username} had no related records and was permanently deleted.",
        student=None,
    )
