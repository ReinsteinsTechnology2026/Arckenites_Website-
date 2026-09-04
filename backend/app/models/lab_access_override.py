import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class LabAccessMode(str, enum.Enum):
    AUTO = "AUTO"
    MANUAL_UNLOCK = "MANUAL_UNLOCK"
    MANUAL_LOCK = "MANUAL_LOCK"


class LabAccessOverride(Base):
    """One row per student — the admin-controlled override on top of the
    automatic slot-based lock/unlock. AUTO defers entirely to whether the
    student currently has a booked lab slot in progress; MANUAL_UNLOCK /
    MANUAL_LOCK pin the status regardless of slot timing until an admin
    changes it again."""

    __tablename__ = "lab_access_overrides"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, unique=True, index=True)

    access_mode: Mapped[LabAccessMode] = mapped_column(
        Enum(LabAccessMode, name="lab_access_mode_enum"), nullable=False, default=LabAccessMode.AUTO
    )
    reason: Mapped[str | None] = mapped_column(String(500), nullable=True)

    updated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
