from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import require_permission
from app.core.lab_access import compute_lab_access, get_or_create_override
from app.database import get_db
from app.models.batch import Batch, BatchEnrollment
from app.models.lab_access_audit import LabAccessAuditLog
from app.models.lab_access_override import LabAccessMode
from app.models.user import RoleEnum, User
from app.schemas.lab_access import (
    AdminLabAccessRowOut,
    LabAccessActionRequest,
    LabAccessAuditEntryOut,
    LabAccessBatchOut,
)

router = APIRouter(prefix="/admin/lab-access", tags=["admin-lab-access"])


def _get_student_or_404(db: Session, student_id: int) -> User:
    user = db.get(User, student_id)
    if user is None or user.role != RoleEnum.student:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Student not found")
    return user


@router.get("/batches", response_model=list[LabAccessBatchOut])
def list_batches(db: Session = Depends(get_db), user: User = Depends(require_permission("lab_access.view"))):
    batches = db.scalars(select(Batch).order_by(Batch.name)).all()
    return [LabAccessBatchOut(id=b.id, name=b.name) for b in batches]


@router.get("/batches/{batch_id}/students", response_model=list[AdminLabAccessRowOut])
def list_batch_students(
    batch_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("lab_access.view")),
):
    batch = db.get(Batch, batch_id)
    if batch is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Batch not found")

    student_ids = db.scalars(
        select(BatchEnrollment.student_id).where(BatchEnrollment.batch_id == batch_id)
    ).all()
    students = db.scalars(select(User).where(User.id.in_(student_ids)).order_by(User.full_name)).all()

    now = datetime.now(timezone.utc)
    rows = []
    for s in students:
        state = compute_lab_access(db, s.id, now)
        updated_by_name = None
        if state["updated_by"]:
            admin = db.get(User, state["updated_by"])
            updated_by_name = admin.full_name if admin else None
        rows.append(AdminLabAccessRowOut(
            student_id=s.id, username=s.username, full_name=s.full_name,
            slot_date=state["slot_date"], slot_start_time=state["slot_start_time"], slot_end_time=state["slot_end_time"],
            slot_state=state["slot_state"], status=state["status"], access_mode=state["access_mode"],
            updated_by_name=updated_by_name, updated_at=state["updated_at"],
        ))
    return rows


def _apply_override(
    db: Session, student_id: int, admin: User, new_mode: LabAccessMode, action: str, reason: str | None,
) -> AdminLabAccessRowOut:
    student = _get_student_or_404(db, student_id)
    now = datetime.now(timezone.utc)

    previous_state = compute_lab_access(db, student_id, now)
    override = get_or_create_override(db, student_id)
    override.access_mode = new_mode
    override.reason = reason
    override.updated_by = admin.id
    db.add(override)
    db.flush()

    new_state = compute_lab_access(db, student_id, now)

    db.add(LabAccessAuditLog(
        student_id=student_id, admin_id=admin.id, action=action,
        previous_status=previous_state["status"], new_status=new_state["status"], reason=reason,
    ))
    db.commit()

    return AdminLabAccessRowOut(
        student_id=student.id, username=student.username, full_name=student.full_name,
        slot_date=new_state["slot_date"], slot_start_time=new_state["slot_start_time"], slot_end_time=new_state["slot_end_time"],
        slot_state=new_state["slot_state"], status=new_state["status"], access_mode=new_state["access_mode"],
        updated_by_name=admin.full_name, updated_at=new_state["updated_at"],
    )


@router.post("/{student_id}/unlock", response_model=AdminLabAccessRowOut)
def unlock_student(
    student_id: int,
    payload: LabAccessActionRequest,
    db: Session = Depends(get_db),
    admin: User = Depends(require_permission("lab_access.manage")),
):
    return _apply_override(db, student_id, admin, LabAccessMode.MANUAL_UNLOCK, "UNLOCK", payload.reason)


@router.post("/{student_id}/lock", response_model=AdminLabAccessRowOut)
def lock_student(
    student_id: int,
    payload: LabAccessActionRequest,
    db: Session = Depends(get_db),
    admin: User = Depends(require_permission("lab_access.manage")),
):
    return _apply_override(db, student_id, admin, LabAccessMode.MANUAL_LOCK, "LOCK", payload.reason)


@router.post("/{student_id}/reset-auto", response_model=AdminLabAccessRowOut)
def reset_student_to_auto(
    student_id: int,
    payload: LabAccessActionRequest,
    db: Session = Depends(get_db),
    admin: User = Depends(require_permission("lab_access.manage")),
):
    return _apply_override(db, student_id, admin, LabAccessMode.AUTO, "RESET_AUTO", payload.reason)


@router.get("/{student_id}/history", response_model=list[LabAccessAuditEntryOut])
def student_history(
    student_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission("lab_access.view")),
):
    _get_student_or_404(db, student_id)
    entries = db.scalars(
        select(LabAccessAuditLog)
        .where(LabAccessAuditLog.student_id == student_id)
        .order_by(LabAccessAuditLog.created_at.desc())
        .limit(100)
    ).all()
    admins = {a.id: a.full_name for a in db.scalars(
        select(User).where(User.id.in_({e.admin_id for e in entries}))
    ).all()} if entries else {}
    return [
        LabAccessAuditEntryOut(
            id=e.id, admin_name=admins.get(e.admin_id, "Unknown"), action=e.action,
            previous_status=e.previous_status, new_status=e.new_status, reason=e.reason, created_at=e.created_at,
        )
        for e in entries
    ]
