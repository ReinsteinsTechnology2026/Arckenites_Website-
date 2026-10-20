from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.batch import Batch, BatchEnrollment, BatchStatusEnum


SECTION_COLUMNS = {
    "job_applications": Batch.job_applications_access_enabled,
    "interview_schedule": Batch.interview_schedule_access_enabled,
}


def student_section_enabled(db: Session, student_id: int, section: str, *, lock: bool = False) -> bool:
    """An enrollment remains valid until removed or its batch is cancelled."""
    query = (
        select(Batch.id)
        .join(BatchEnrollment, BatchEnrollment.batch_id == Batch.id)
        .where(
            BatchEnrollment.student_id == student_id,
            Batch.status != BatchStatusEnum.cancelled,
            SECTION_COLUMNS[section].is_(True),
        )
        .limit(1)
    )
    if lock:
        query = query.with_for_update(of=Batch)
    return db.scalar(query) is not None


def require_student_section(db: Session, student_id: int, section: str, *, lock: bool = False) -> None:
    if not student_section_enabled(db, student_id, section, lock=lock):
        label = "Job Applications" if section == "job_applications" else "Interview Schedule"
        raise HTTPException(status_code=403, detail=f"{label} section is currently unavailable")
