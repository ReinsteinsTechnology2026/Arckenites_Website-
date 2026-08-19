import enum
from datetime import date, datetime, time

from sqlalchemy import Date, DateTime, Enum, ForeignKey, Integer, String, Time, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class BatchTypeEnum(str, enum.Enum):
    online = "online"
    offline = "offline"
    hybrid = "hybrid"


class BatchStatusEnum(str, enum.Enum):
    upcoming = "upcoming"
    active = "active"
    paused = "paused"
    completed = "completed"
    cancelled = "cancelled"


class Batch(Base):
    """Student -> Batch -> Trainer is intentional: students are never linked
    to a trainer directly, so reassigning a batch's trainer doesn't require
    touching every student's record. Course is free text for now since
    there's no standalone Courses module/table yet (same "Soon" state as the
    Courses sidebar item) — swap for a course_id FK once that module exists."""

    __tablename__ = "batches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    course: Mapped[str | None] = mapped_column(String(200), nullable=True)
    batch_type: Mapped[BatchTypeEnum] = mapped_column(
        Enum(BatchTypeEnum, name="batch_type_enum"), nullable=False, default=BatchTypeEnum.offline
    )
    status: Mapped[BatchStatusEnum] = mapped_column(
        Enum(BatchStatusEnum, name="batch_status_enum"), nullable=False, default=BatchStatusEnum.upcoming
    )

    # Primary trainer only for now — kept on its own nullable column (not a
    # join table) so a future "co-trainers" feature can add one without a
    # breaking migration.
    trainer_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)

    # Which program this batch runs under — the middle link in
    # Program -> Batch -> Students + Trainer. Nullable because batches
    # created before the Programs module existed have no program set.
    program_id: Mapped[int | None] = mapped_column(ForeignKey("programs.id"), nullable=True, index=True)

    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    max_capacity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    class_days: Mapped[str | None] = mapped_column(String(100), nullable=True)  # e.g. "Mon,Wed,Fri"
    start_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    end_time: Mapped[time | None] = mapped_column(Time, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    trainer: Mapped["User"] = relationship(foreign_keys=[trainer_id])
    program: Mapped["Program"] = relationship(foreign_keys=[program_id])
    enrollments: Mapped[list["BatchEnrollment"]] = relationship(
        back_populates="batch", cascade="all, delete-orphan"
    )


class BatchEnrollment(Base):
    """Student <-> Batch, many-to-many (a student can be in several batches
    at once) with a joined_at per membership."""

    __tablename__ = "batch_enrollments"
    __table_args__ = (UniqueConstraint("batch_id", "student_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("batches.id"), nullable=False, index=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    batch: Mapped["Batch"] = relationship(back_populates="enrollments")
    student: Mapped["User"] = relationship(foreign_keys=[student_id])
