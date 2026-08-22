import io
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from openpyxl import Workbook
from sqlalchemy import delete, or_, select, update
from sqlalchemy.orm import Session, selectinload

from app.core.deps import require_permission
from app.core.security import hash_password
from app.crud.audit import write_audit_event
from app.crud.user import generate_username
from app.database import get_db
from app.models.audit_log import AuthAuditLog, AuthEventType
from app.models.batch import BatchEnrollment
from app.models.chat import Conversation, DirectMessage
from app.models.interview_schedule import InterviewSchedule
from app.models.program import ProgramEnrollment
from app.models.session import AuthSession
from app.models.student import PROGRAM_LABELS, ProgramEnum, StudentProfile
from app.models.user import RoleEnum, User
from app.schemas.admin_students import CreateStudentRequest, StudentAdminOut, UpdateStudentRequest

router = APIRouter(prefix="/admin", tags=["admin-students"])


def _client_meta(request: Request) -> tuple[str, str]:
    ip = request.client.host if request.client else "unknown"
    ua = request.headers.get("user-agent", "unknown")
    return ip, ua


def _get_student_or_404(db: Session, student_id: int) -> User:
    user = db.get(User, student_id)
    if user is None or user.role != RoleEnum.student:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Student not found")
    return user


@router.post("/students", response_model=StudentAdminOut, status_code=201)
def create_student(
    payload: CreateStudentRequest,
    request: Request,
    db: Session = Depends(get_db),
    actor: User = Depends(require_permission("students.create")),
):
    username = generate_username(db, payload.full_name, RoleEnum.student)

    user = User(
        username=username,
        password_hash=hash_password(payload.temp_password),
        full_name=payload.full_name,
        role=RoleEnum.student,
        must_change_password=True,
    )
    db.add(user)
    db.flush()

    program = ProgramEnum(payload.program) if payload.program else None
    db.add(StudentProfile(user_id=user.id, program=program))
    db.commit()
    db.refresh(user)

    ip, ua = _client_meta(request)
    write_audit_event(
        db, AuthEventType.student_created, ip, ua, user=actor, detail=f"Created student {username}",
        module="students", target=username, status="success",
    )

    return StudentAdminOut.model_validate(user)


@router.get("/students", response_model=list[StudentAdminOut])
def list_students(
    db: Session = Depends(get_db),
    _actor: User = Depends(require_permission("students.view")),
):
    users = db.scalars(
        select(User)
        .where(User.role == RoleEnum.student)
        .options(selectinload(User.student_profile))
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
        .options(selectinload(User.student_profile))
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
        program_label = PROGRAM_LABELS.get(profile.program, "") if profile and profile.program else ""
        current_role = profile.current_role.value if profile and profile.current_role else ""
        ws.append([
            u.username,
            u.full_name,
            profile.phone if profile else "",
            profile.email if profile else "",
            current_role,
            program_label,
            "Active" if u.is_active else "Inactive",
            "Yes" if u.must_change_password else "No",
            u.created_at.strftime("%Y-%m-%d %H:%M") if u.created_at else "",
        ])

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
    if payload.is_active is not None:
        if payload.is_active != user.is_active:
            write_audit_event(
                db, AuthEventType.student_enabled if payload.is_active else AuthEventType.student_disabled,
                ip, ua, user=actor, detail=f"{'Enabled' if payload.is_active else 'Disabled'} student {user.username}",
                module="students", target=user.username, status="success",
            )
        user.is_active = payload.is_active
    if payload.program is not None:
        user.student_profile.program = ProgramEnum(payload.program)
    if payload.reset_temp_password is not None:
        user.password_hash = hash_password(payload.reset_temp_password)
        user.must_change_password = True
        user.failed_login_count = 0
        user.locked_until = None
        write_audit_event(
            db, AuthEventType.student_password_reset, ip, ua, user=actor,
            detail=f"Reset password for student {user.username}", module="students", target=user.username,
            status="success",
        )

    if payload.full_name is not None or payload.program is not None:
        write_audit_event(
            db, AuthEventType.student_updated, ip, ua, user=actor, detail=f"Edited student {user.username}",
            module="students", target=user.username, status="success",
        )

    db.add(user)
    db.commit()
    db.refresh(user)

    return StudentAdminOut.model_validate(user)


@router.delete("/students/{student_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_student(
    student_id: int,
    request: Request,
    db: Session = Depends(get_db),
    actor: User = Depends(require_permission("students.delete")),
):
    user = _get_student_or_404(db, student_id)
    username = user.username

    # Detach (don't delete) audit history so security records survive account
    # removal — auth_audit_log.user_id is nullable for exactly this reason.
    db.execute(update(AuthAuditLog).where(AuthAuditLog.user_id == user.id).values(user_id=None))
    db.execute(delete(AuthSession).where(AuthSession.user_id == user.id))

    # Chat rows have no ON DELETE CASCADE to users.id, so any conversation this
    # account took part in (as either side) must be cleared first or the
    # user delete below fails on a foreign key violation.
    convo_ids = db.scalars(
        select(Conversation.id).where(or_(Conversation.user_a_id == user.id, Conversation.user_b_id == user.id))
    ).all()
    if convo_ids:
        db.execute(delete(DirectMessage).where(DirectMessage.conversation_id.in_(convo_ids)))
        db.execute(delete(Conversation).where(Conversation.id.in_(convo_ids)))

    # Same story for batch/program enrollments — no ON DELETE CASCADE to
    # users.id, so a student who's allocated to any batch or enrolled in any
    # program must be detached from both before the user row can go.
    db.execute(delete(BatchEnrollment).where(BatchEnrollment.student_id == user.id))
    db.execute(delete(ProgramEnrollment).where(ProgramEnrollment.student_id == user.id))
    db.execute(delete(InterviewSchedule).where(InterviewSchedule.student_id == user.id))

    db.delete(user)  # cascades to student_profile via the User.student_profile relationship
    db.commit()

    ip, ua = _client_meta(request)
    write_audit_event(
        db, AuthEventType.student_deleted, ip, ua, user=actor, detail=f"Deleted student {username}",
        module="students", target=username, status="success",
    )
    return None
