"""Durable, transactionally queued cleanup of exclusively owned uploads."""
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class FileCleanupJob(Base):
    __tablename__ = "file_cleanup_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    storage_kind: Mapped[str] = mapped_column(String(30), nullable=False)
    storage_reference: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
