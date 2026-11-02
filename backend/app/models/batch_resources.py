import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class ClassVideo(Base):
    """An entry in a batch's Class Videos section. Two kinds:

    * LINK - a trainer/admin-posted URL (the original behaviour, unchanged).
    * RECORDING - an Arckenites class recording that was verified, stored and
      published automatically. It has no URL: students watch it through an
      authorized streaming endpoint, so there is never a storage link to leak.
      `recording_id` is unique, so a retried publish job can never create a
      second entry for the same recording.
    """

    __tablename__ = "class_videos"
    __table_args__ = (
        CheckConstraint("video_url IS NOT NULL OR recording_id IS NOT NULL", name="ck_class_videos_has_target"),
        CheckConstraint("source IN ('LINK', 'RECORDING')", name="ck_class_videos_source"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("batches.id"), nullable=False, index=True)

    title: Mapped[str] = mapped_column(String(200), nullable=False)
    video_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="LINK", server_default="LINK")
    recording_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("class_recordings.id"), nullable=True, unique=True
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    batch: Mapped["Batch"] = relationship(back_populates="videos")


class StudyMaterial(Base):
    """A document/slide/notes link posted for a batch — same link-not-file
    reasoning as ClassVideo."""

    __tablename__ = "study_materials"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("batches.id"), nullable=False, index=True)

    title: Mapped[str] = mapped_column(String(200), nullable=False)
    file_url: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    batch: Mapped["Batch"] = relationship(back_populates="materials")
