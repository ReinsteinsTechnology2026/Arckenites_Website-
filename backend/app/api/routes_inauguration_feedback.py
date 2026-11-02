from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, Request, status
from sqlalchemy.orm import Session

from app.config import settings
from app.core.feedback_sheets import sync_new_submission
from app.core.rate_limit import limiter
from app.database import get_db
from app.models.inauguration_feedback import InaugurationFeedback
from app.schemas.inauguration_feedback import InaugurationFeedbackRequest

router = APIRouter(prefix="/feedback", tags=["feedback"])


@router.post("/inauguration", status_code=status.HTTP_201_CREATED)
@limiter.limit("10/minute")
def submit_inauguration_feedback(
    request: Request,
    payload: InaugurationFeedbackRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    # Saved to PostgreSQL first. The retry time keeps the background worker from racing the
    # immediate sync below; if this process dies before it runs, the worker picks the row up.
    retry_at = datetime.now(timezone.utc) + timedelta(seconds=settings.feedback_sheets_retry_interval_seconds)
    row = InaugurationFeedback(**payload.model_dump(), sheets_next_attempt_at=retry_at)
    db.add(row)
    db.commit()
    # The Google Sheet copy never changes the response: a sync problem is retried, not shown.
    background_tasks.add_task(sync_new_submission, row.id)
    return {"detail": "Thank you for your valuable feedback!"}
