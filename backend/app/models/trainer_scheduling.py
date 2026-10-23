from datetime import date, datetime, time

from sqlalchemy import (
    JSON, Boolean, CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, SmallInteger, String, Text, Time,
    UniqueConstraint, func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

AVAILABILITY_KINDS = ("available", "preferred", "unavailable", "leave")
BLOCKING_KINDS = ("unavailable", "leave")
ENTRY_CATEGORIES = ("holiday", "leave", "personal", "schedule_change", "other")


class TrainerAvailabilityRule(Base):
    """A recurring weekly window ("Mon-Fri 09:00-17:00"). One row per weekday.
    Times are wall-clock in the business timezone (settings.scheduling_timezone),
    the same convention batches.start_time / class_sessions.start_time already use,
    so DST shifts never move a trainer's working day."""

    __tablename__ = "trainer_availability_rules"
    __table_args__ = (
        CheckConstraint("weekday BETWEEN 0 AND 6", name="ck_tar_weekday"),
        CheckConstraint("end_time > start_time", name="ck_tar_time_order"),
        CheckConstraint("kind IN ('available', 'preferred')", name="ck_tar_kind"),
        CheckConstraint("effective_until IS NULL OR effective_until >= effective_from", name="ck_tar_date_order"),
        Index("ix_tar_trainer_weekday", "trainer_id", "weekday"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    trainer_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    weekday: Mapped[int] = mapped_column(SmallInteger, nullable=False)  # Monday = 0
    start_time: Mapped[time] = mapped_column(Time, nullable=False)
    end_time: Mapped[time] = mapped_column(Time, nullable=False)
    kind: Mapped[str] = mapped_column(String(12), nullable=False, default="available", server_default="available")
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_until: Mapped[date | None] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class TrainerAvailabilityEntry(Base):
    """A one-off dated window: extra availability, preferred hours, or an
    exception (unavailable / leave / holiday). Stored as UTC instants."""

    __tablename__ = "trainer_availability_entries"
    __table_args__ = (
        CheckConstraint("ends_at > starts_at", name="ck_tae_time_order"),
        CheckConstraint("kind IN ('available', 'preferred', 'unavailable', 'leave')", name="ck_tae_kind"),
        Index("ix_tae_trainer_window", "trainer_id", "starts_at", "ends_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    trainer_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    kind: Mapped[str] = mapped_column(String(12), nullable=False)
    category: Mapped[str | None] = mapped_column(String(20), nullable=True)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    note: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class TrainerExpertise(Base):
    """Which programs a trainer can teach. The repo had no trainer-skill data,
    and batches link to a Program (batches.program_id), so program is the
    smallest honest unit of "course expertise"."""

    __tablename__ = "trainer_expertise"
    __table_args__ = (UniqueConstraint("trainer_id", "program_id", name="uq_trainer_expertise"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    trainer_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    program_id: Mapped[int] = mapped_column(ForeignKey("programs.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class BatchTrainerAssignment(Base):
    """Append-only assignment history. `idempotency_key` makes a retried
    confirm request return the original result instead of re-assigning."""

    __tablename__ = "batch_trainer_assignments"
    __table_args__ = (
        CheckConstraint("action IN ('assigned', 'changed', 'unassigned')", name="ck_bta_action"),
        Index("ix_bta_batch_created", "batch_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("batches.id", ondelete="CASCADE"), nullable=False)
    action: Mapped[str] = mapped_column(String(12), nullable=False)
    from_trainer_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    to_trainer_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    was_override: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    overridden_conflicts: Mapped[list | None] = mapped_column(JSON, nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SchedulingNotice(Base):
    """In-portal notice. recipient_id NULL = visible to every admin with
    scheduling access; otherwise addressed to that one user."""

    __tablename__ = "scheduling_notices"
    __table_args__ = (
        CheckConstraint("status IN ('open', 'resolved')", name="ck_sn_status"),
        Index("ix_sn_recipient_status", "recipient_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    recipient_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    trainer_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    batch_id: Mapped[int | None] = mapped_column(ForeignKey("batches.id", ondelete="CASCADE"), nullable=True)
    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="open", server_default="open")
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
