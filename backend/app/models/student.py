import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class ProgramEnum(str, enum.Enum):
    official_certification = "official_certification"
    corporate_training = "corporate_training"
    institutional = "institutional"
    placement_training = "placement_training"
    trainers_program = "trainers_program"
    internship = "internship"
    interview_crack = "interview_crack"
    job_assist = "job_assist"
    college_projects = "college_projects"


PROGRAM_LABELS: dict[ProgramEnum, str] = {
    ProgramEnum.official_certification: "Official Certification Program",
    ProgramEnum.corporate_training: "Corporate Training Program",
    ProgramEnum.institutional: "Institutional Program",
    ProgramEnum.placement_training: "Placement Training Program",
    ProgramEnum.trainers_program: "Arckenites Trainers Program",
    ProgramEnum.internship: "Internship Program",
    ProgramEnum.interview_crack: "Interview 'n' Crack Program",
    ProgramEnum.job_assist: "Arckenites Job Assist Program",
    ProgramEnum.college_projects: "College Projects Program",
}


class CurrentRoleEnum(str, enum.Enum):
    student = "student"
    employer = "employer"


class StudentProfile(Base):
    __tablename__ = "student_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True, nullable=False)

    # student_code is nullable/unassigned until Phase 5 (real admin-driven provisioning) fills it in.
    student_code: Mapped[str | None] = mapped_column(String(50), unique=True, nullable=True)
    # phone doubles as "Mobile Number" collected during the post-first-login onboarding step.
    phone: Mapped[str | None] = mapped_column(String(30), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    address: Mapped[str | None] = mapped_column(String(500), nullable=True)
    current_role: Mapped[CurrentRoleEnum | None] = mapped_column(Enum(CurrentRoleEnum, name="current_role_enum"), nullable=True)

    # Null until the student completes the post-first-login onboarding step.
    program: Mapped[ProgramEnum | None] = mapped_column(Enum(ProgramEnum, name="program_enum"), nullable=True)

    # course_id / batch_id FKs are intentionally NOT added yet — courses/batches
    # tables don't exist until a later phase. Add via a new Alembic migration then.

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    user: Mapped["User"] = relationship(back_populates="student_profile")
