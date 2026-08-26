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

    # Storage reference (filename under uploads/profile_photos/), never a raw
    # URL — resolved to a servable path only via photo_url/routes_profile.py.
    photo_path: Mapped[str | None] = mapped_column(String(255), nullable=True)

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

    # ------------------------------------------------------------------
    # Private-detail passthroughs — every one of these is only ever meant
    # to reach the schema/route that represents "my own profile" (auth/me,
    # login) or an admin-only view. Anywhere a student/trainer is shown to
    # another student/trainer, build a PublicProfileOut (photo_url +
    # full_name only) instead of serializing the User row directly.
    # ------------------------------------------------------------------

    @property
    def phone(self) -> str | None:
        if self.role == RoleEnum.student and self.student_profile:
            return self.student_profile.phone
        if self.role == RoleEnum.staff and self.staff_profile:
            return self.staff_profile.phone
        return None

    @property
    def email(self) -> str | None:
        if self.role == RoleEnum.student and self.student_profile:
            return self.student_profile.email
        if self.role == RoleEnum.staff and self.staff_profile:
            return self.staff_profile.email
        if self.role == RoleEnum.admin and self.admin_profile:
            return self.admin_profile.email
        return None

    @property
    def address(self) -> str | None:
        if self.role == RoleEnum.student and self.student_profile:
            return self.student_profile.address
        if self.role == RoleEnum.staff and self.staff_profile:
            return self.staff_profile.address
        return None

    @property
    def student_id(self) -> str | None:
        if self.role == RoleEnum.student and self.student_profile:
            return self.student_profile.student_code
        return None

    @property
    def trainer_id(self) -> str | None:
        if self.role == RoleEnum.staff and self.staff_profile:
            return self.staff_profile.staff_code
        return None

    @property
    def designation(self) -> str | None:
        if self.role == RoleEnum.staff and self.staff_profile:
            return self.staff_profile.designation
        return None

    @property
    def department(self) -> str | None:
        if self.role == RoleEnum.staff and self.staff_profile:
            return self.staff_profile.department
        return None

    @property
    def photo_url(self) -> str | None:
        """A path relative to the API root (no /api prefix — callers already
        prepend that, same convention as every other endpoint path returned
        to the frontend), resolved by GET /users/{id}/photo. Never a
        filesystem path — only this URL is exposed, never photo_path."""
        return f"/users/{self.id}/photo" if self.photo_path else None
