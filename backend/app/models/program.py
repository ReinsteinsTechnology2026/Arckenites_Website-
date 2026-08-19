import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class ProgramModeEnum(str, enum.Enum):
    online = "online"
    offline = "offline"
    hybrid = "hybrid"


class ProgramStatusEnum(str, enum.Enum):
    active = "active"
    inactive = "inactive"


class EnrollmentStatusEnum(str, enum.Enum):
    pending = "pending"
    approved = "approved"
    allocated = "allocated"
    active = "active"
    completed = "completed"
    withdrawn = "withdrawn"
    rejected = "rejected"


class Program(Base):
    """The admin-managed catalogue of what a student can enrol for.

    `slug` deliberately mirrors the values of ProgramEnum in models/student.py:
    that enum still drives the student's own onboarding pick and the
    student_profiles.program column, both of which are left untouched here.
    The slug is what ties a row in this table back to that existing data, so
    the two representations can coexist without one having to be rewritten.

    Program -> Batch -> Students + Trainer is the intended chain: a program
    has many batches (batches.program_id), and each batch carries its own
    trainer and its own group of students. Nothing in this module links a
    student to a trainer directly.
    """

    __tablename__ = "programs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(80), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    code: Mapped[str | None] = mapped_column(String(40), unique=True, nullable=True)

    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    category: Mapped[str | None] = mapped_column(String(120), nullable=True)
    duration: Mapped[str | None] = mapped_column(String(120), nullable=True)
    eligibility: Mapped[str | None] = mapped_column(Text, nullable=True)
    objectives: Mapped[str | None] = mapped_column(Text, nullable=True)
    max_capacity: Mapped[int | None] = mapped_column(Integer, nullable=True)

    mode: Mapped[ProgramModeEnum] = mapped_column(
        Enum(ProgramModeEnum, name="program_mode_enum"), nullable=False, default=ProgramModeEnum.offline
    )
    status: Mapped[ProgramStatusEnum] = mapped_column(
        Enum(ProgramStatusEnum, name="program_status_enum"), nullable=False, default=ProgramStatusEnum.active
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    enrollments: Mapped[list["ProgramEnrollment"]] = relationship(
        back_populates="program", cascade="all, delete-orphan"
    )


class ProgramEnrollment(Base):
    """One row per (student, program). Many-to-many on purpose: a student can
    hold an active enrolment in one program while carrying a completed or
    withdrawn one in another, which is what makes the status history useful.

    Batch allocation is NOT stored here — it lives in batch_enrollments, so a
    student can move between batches inside the same program without this row
    changing anything except its status.
    """

    __tablename__ = "program_enrollments"
    __table_args__ = (UniqueConstraint("program_id", "student_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    program_id: Mapped[int] = mapped_column(ForeignKey("programs.id"), nullable=False, index=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)

    status: Mapped[EnrollmentStatusEnum] = mapped_column(
        Enum(EnrollmentStatusEnum, name="enrollment_status_enum"), nullable=False, default=EnrollmentStatusEnum.pending
    )
    enrolled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    program: Mapped["Program"] = relationship(back_populates="enrollments")
    student: Mapped["User"] = relationship(foreign_keys=[student_id])
