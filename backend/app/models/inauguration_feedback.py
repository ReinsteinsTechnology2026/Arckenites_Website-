import uuid
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class InaugurationFeedback(Base):
    """Feedback submitted from the public Inauguration Session feedback page."""

    __tablename__ = "inauguration_feedback"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Stable public identifier; it is the de-duplication key for the Google Sheet row.
    submission_id: Mapped[str] = mapped_column(String(36), unique=True, index=True, default=lambda: str(uuid.uuid4()))

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    domain: Mapped[str] = mapped_column(String(50), nullable=False)
    domain_other: Mapped[str | None] = mapped_column(String(200), nullable=True)
    overall_session: Mapped[str] = mapped_column(String(30), nullable=False)
    usefulness: Mapped[str] = mapped_column(String(30), nullable=False)
    learned_something: Mapped[str] = mapped_column(String(10), nullable=False)
    liked_topic: Mapped[str] = mapped_column(String(30), nullable=False)
    liked_topic_other: Mapped[str | None] = mapped_column(String(200), nullable=True)
    attend_future: Mapped[str] = mapped_column(String(10), nullable=False)
    suggestion: Mapped[str | None] = mapped_column(Text, nullable=True)
    contact_preference: Mapped[str] = mapped_column(String(10), nullable=False)
    contact_number: Mapped[str | None] = mapped_column(String(20), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)

    # Google Sheets mirror (app/core/feedback_sheets.py). PostgreSQL is the source of truth;
    # these columns only track whether the spreadsheet copy has been written.
    # PENDING = not yet synced (will be retried), SYNCED, FAILED = retries exhausted.
    sheets_sync_status: Mapped[str] = mapped_column(String(10), nullable=False, default='PENDING', server_default='PENDING', index=True)
    sheets_sync_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default='0')
    sheets_next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sheets_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Short error code only (e.g. http_500, timeout). Never a response body or form data.
    sheets_last_error: Mapped[str | None] = mapped_column(String(100), nullable=True)
