import enum
from datetime import date, datetime, time

from sqlalchemy import Date, DateTime, Enum, ForeignKey, Integer, String, Text, Time, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class InterviewModeEnum(str, enum.Enum):
    online = "online"
    offline = "offline"


class InterviewStatusEnum(str, enum.Enum):
    scheduled = "scheduled"
    completed = "completed"
    cancelled = "cancelled"


class InterviewSchedule(Base):
    """A placement interview slot for one student — tied directly to the
    student (not a batch), since a company interviews a specific person,
    not a whole batch."""

    __tablename__ = "interview_schedules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)

    company_name: Mapped[str] = mapped_column(String(200), nullable=False)
    role: Mapped[str | None] = mapped_column(String(200), nullable=True)
    interview_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    interview_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    mode: Mapped[InterviewModeEnum] = mapped_column(
        Enum(InterviewModeEnum, name="interview_mode_enum"), nullable=False, default=InterviewModeEnum.online
    )
    location_or_link: Mapped[str | None] = mapped_column(String(500), nullable=True)
    status: Mapped[InterviewStatusEnum] = mapped_column(
        Enum(InterviewStatusEnum, name="interview_status_enum"), nullable=False, default=InterviewStatusEnum.scheduled
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    student: Mapped["User"] = relationship(foreign_keys=[student_id])
