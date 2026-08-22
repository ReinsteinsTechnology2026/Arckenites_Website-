from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class LabAccess(Base):
    """A lab environment link + credentials posted for a batch — e.g. a
    remote VM or cloud sandbox students use for hands-on practice."""

    __tablename__ = "lab_access"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("batches.id"), nullable=False, index=True)

    title: Mapped[str] = mapped_column(String(200), nullable=False)
    access_url: Mapped[str] = mapped_column(String(500), nullable=False)
    username: Mapped[str | None] = mapped_column(String(200), nullable=True)
    password: Mapped[str | None] = mapped_column(String(200), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    batch: Mapped["Batch"] = relationship(back_populates="lab_access_entries")


class ClassVideo(Base):
    """A recorded class video posted for a batch — a link, not a stored
    file: video hosting/storage is a separate infra decision for later."""

    __tablename__ = "class_videos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("batches.id"), nullable=False, index=True)

    title: Mapped[str] = mapped_column(String(200), nullable=False)
    video_url: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

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
