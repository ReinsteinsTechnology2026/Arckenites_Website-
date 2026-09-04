from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.lab_access_override import LabAccessMode, LabAccessOverride
from app.models.lab_slot_booking import LabSlotBooking


def get_or_create_override(db: Session, student_id: int) -> LabAccessOverride:
    override = db.scalar(select(LabAccessOverride).where(LabAccessOverride.student_id == student_id))
    if override is None:
        override = LabAccessOverride(student_id=student_id, access_mode=LabAccessMode.AUTO)
        db.add(override)
        db.flush()
    return override


def _relevant_slot(db: Session, student_id: int, now: datetime):
    """Returns (booking, slot_state) — the booking most relevant to `now`:
    the one in progress, else the next upcoming one, else the most recently
    completed one, else None."""
    today = now.date()
    current_time = now.time()

    upcoming_or_current = db.scalars(
        select(LabSlotBooking)
        .where(LabSlotBooking.student_id == student_id, LabSlotBooking.slot_date >= today)
        .order_by(LabSlotBooking.slot_date, LabSlotBooking.start_time)
    ).all()

    for b in upcoming_or_current:
        if b.slot_date == today and b.start_time <= current_time <= b.end_time:
            return b, "ACTIVE"
    for b in upcoming_or_current:
        if b.slot_date > today or (b.slot_date == today and b.start_time > current_time):
            return b, "NOT_STARTED"

    last_completed = db.scalar(
        select(LabSlotBooking)
        .where(
            LabSlotBooking.student_id == student_id,
            (LabSlotBooking.slot_date < today)
            | ((LabSlotBooking.slot_date == today) & (LabSlotBooking.end_time <= current_time)),
        )
        .order_by(LabSlotBooking.slot_date.desc(), LabSlotBooking.end_time.desc())
    )
    if last_completed is not None:
        return last_completed, "COMPLETED"

    return None, "NO_SLOT"


def compute_lab_access(db: Session, student_id: int, now: datetime | None = None) -> dict:
    """The single source of truth for a student's current lab lock state —
    used by both the student-facing status check and the admin table, and
    by the 403 enforcement on actual lab resource endpoints."""
    now = now or datetime.now(timezone.utc)
    override = get_or_create_override(db, student_id)
    slot, slot_state = _relevant_slot(db, student_id, now)

    if override.access_mode == LabAccessMode.MANUAL_UNLOCK:
        access_status = "UNLOCKED"
    elif override.access_mode == LabAccessMode.MANUAL_LOCK:
        access_status = "LOCKED"
    else:
        access_status = "UNLOCKED" if slot_state == "ACTIVE" else "LOCKED"

    return {
        "status": access_status,
        "access_mode": override.access_mode.value,
        "slot_state": slot_state,
        "slot_date": slot.slot_date if slot else None,
        "slot_start_time": slot.start_time if slot else None,
        "slot_end_time": slot.end_time if slot else None,
        "updated_by": override.updated_by,
        "updated_at": override.updated_at,
    }
