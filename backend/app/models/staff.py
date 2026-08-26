from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

# The only permission keys the admin Trainers UI is allowed to show/persist —
# each corresponds to a real (existing or sidebar-planned) area of the app.
# Nothing here is enforced anywhere yet (attendance/assignments/courses/batches
# don't have pages yet), same as StudentProfile.program before onboarding
# existed: a real, persisted field with no consumer yet beyond display.
PERMISSION_KEYS: list[str] = [
    "view_students",
    "manage_students",
    "view_attendance",
    "mark_attendance",
    "view_assignments",
    "manage_assignments",
    "view_courses",
    "view_batches",
]


class StaffProfile(Base):
    __tablename__ = "staff_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True, nullable=False)

    staff_code: Mapped[str | None] = mapped_column(String(50), unique=True, nullable=True)
    designation: Mapped[str | None] = mapped_column(String(120), nullable=True)
    department: Mapped[str | None] = mapped_column(String(120), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(30), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    address: Mapped[str | None] = mapped_column(String(500), nullable=True)
    permissions: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    user: Mapped["User"] = relationship(back_populates="staff_profile")
