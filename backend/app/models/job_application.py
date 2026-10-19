import enum
from datetime import date, datetime

from sqlalchemy import Date, DateTime, Enum, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class JobApplicationStatus(str, enum.Enum):
    draft = "draft"
    published = "published"
    archived = "archived"


class JobReactionKind(str, enum.Enum):
    like = "like"
    love = "love"
    celebrate = "celebrate"
    interested = "interested"


class JobApplication(Base):
    __tablename__ = "job_applications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    company: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    eligibility: Mapped[str | None] = mapped_column(Text)
    application_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    deadline: Mapped[date | None] = mapped_column(Date)
    status: Mapped[JobApplicationStatus] = mapped_column(
        Enum(JobApplicationStatus, name="job_application_status_enum"), nullable=False,
        default=JobApplicationStatus.draft,
    )
    is_enabled: Mapped[bool] = mapped_column(nullable=False, default=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (Index("ix_job_applications_visibility", "status", "is_enabled", "published_at"),)


class JobApplicationReaction(Base):
    __tablename__ = "job_application_reactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("job_applications.id", ondelete="CASCADE"), nullable=False)
    student_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    kind: Mapped[JobReactionKind] = mapped_column(Enum(JobReactionKind, name="job_reaction_kind_enum"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("job_id", "student_id", name="uq_job_reaction_student"),
        Index("ix_job_reactions_job_kind", "job_id", "kind"),
    )
