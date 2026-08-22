from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.deps import require_permission
from app.database import get_db
from app.models.batch import Batch
from app.models.class_session import ClassSession
from app.models.user import User
from app.schemas.class_sessions import AdminClassSessionOut

router = APIRouter(prefix="/admin/class-schedule", tags=["admin-class-schedule"])


@router.get("", response_model=list[AdminClassSessionOut])
def list_all_sessions(db: Session = Depends(get_db), _actor: User = Depends(require_permission("batches.view"))):
    """Every scheduled class across every batch, site-wide — the consolidated
    view an admin needs to see which classes and which batches are scheduled
    without opening each batch individually."""
    rows = db.scalars(
        select(ClassSession)
        .options(
            selectinload(ClassSession.batch).selectinload(Batch.trainer),
            selectinload(ClassSession.batch).selectinload(Batch.program),
        )
        .order_by(ClassSession.session_date, ClassSession.start_time)
    ).all()

    return [
        AdminClassSessionOut(
            id=s.id, batch_id=s.batch_id, batch_name=s.batch.name,
            title=s.title, session_date=s.session_date, start_time=s.start_time,
            end_time=s.end_time, meeting_link=s.meeting_link, notes=s.notes, created_at=s.created_at,
            trainer_name=s.batch.trainer.full_name if s.batch.trainer else None,
            program_name=s.batch.program.name if s.batch.program else None,
        )
        for s in rows
    ]
