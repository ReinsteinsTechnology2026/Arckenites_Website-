import enum
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class RoleEnum(str, enum.Enum):
    admin = "admin"
    staff = "staff"
    student = "student"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(120), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    role: Mapped[RoleEnum] = mapped_column(Enum(RoleEnum, name="role_enum"), nullable=False)
    # Only meaningful when role == admin — the RBAC tier (Super Admin / Admin /
    # Support Admin / a custom role). Nullable so staff/student rows are
    # simply NULL, no backfill needed for them.
    admin_role_id: Mapped[int | None] = mapped_column(ForeignKey("admin_roles.id"), nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    must_change_password: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    failed_login_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    password_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    student_profile: Mapped["StudentProfile"] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    staff_profile: Mapped["StaffProfile"] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    admin_role: Mapped["AdminRole"] = relationship(foreign_keys=[admin_role_id])
    admin_profile: Mapped["AdminProfile"] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan"
    )

    @property
    def program(self) -> str | None:
        """Lets UserOut/MeResponse (from_attributes=True) surface the student's
        chosen program without every caller having to reach into student_profile."""
        if self.role == RoleEnum.student and self.student_profile and self.student_profile.program:
            return self.student_profile.program.value
        return None

    @property
    def profile_completed(self) -> bool:
        """Whether the student has been through the post-first-login "tell us
        about yourself" step (mobile/email/current_role). Program is no
        longer part of that step — admins set it at account creation — so
        this can't just check `program` the way onboarding gating used to."""
        if self.role != RoleEnum.student:
            return True
        return bool(self.student_profile and self.student_profile.current_role is not None)
