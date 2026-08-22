from datetime import date, datetime, time

from sqlalchemy import Date, DateTime, ForeignKey, Integer, String, Text, Time, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class ClassSession(Base):
    """One scheduled class for a batch — e.g. "Aug 25, 6-8 PM: Python Basics".
    Deliberately hangs off Batch, not Program: a batch already carries its
    trainer and students, and sessions are a batch-level concept that any
    program's batches can use, not something specific to one program."""

    __tablename__ = "class_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("batches.id"), nullable=False, index=True)

    title: Mapped[str] = mapped_column(String(200), nullable=False)
    session_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    start_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    end_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    meeting_link: Mapped[str | None] = mapped_column(String(500), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    batch: Mapped["Batch"] = relationship(back_populates="sessions")
