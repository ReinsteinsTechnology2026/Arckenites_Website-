import re

from pydantic import BaseModel, Field, field_validator

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=1, max_length=128)


class AdminRoleSummary(BaseModel):
    id: int
    name: str
    slug: str

    model_config = {"from_attributes": True}


class UserOut(BaseModel):
    """The caller's OWN account, always — get_current_user resolves "me" from
    the auth token, so this is never used to look up someone else. That's
    what makes it safe to include private fields (email/phone/ids) here that
    must never appear in any endpoint describing another user; those use
    PublicProfileOut (schemas/profile.py) instead."""
    id: int
    username: str
    full_name: str
    role: str
    is_active: bool = True
    must_change_password: bool
    photo_url: str | None = None
    email: str | None = None
    phone: str | None = None
    address: str | None = None
    student_id: str | None = None
    trainer_id: str | None = None
    designation: str | None = None
    department: str | None = None
    program: str | None = None
    profile_completed: bool = True
    # Populated explicitly by the route (not bare from_attributes) since it
    # needs a fresh permissions-table query — see crud/permissions.py.
    admin_role: AdminRoleSummary | None = None
    permissions: list[str] = Field(default_factory=list)

    model_config = {"from_attributes": True}


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


class MeResponse(UserOut):
    pass


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


class UpdateMyProfileRequest(BaseModel):
    """A student or trainer editing their own contact details from the
    Profile screen — distinct from CompleteProfileRequest (the one-time
    post-first-login onboarding step): this is the ongoing, repeatable edit."""
    full_name: str = Field(min_length=1, max_length=200)
    email: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=30)
    address: str | None = Field(default=None, max_length=500)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str | None) -> str | None:
        if value is None or value.strip() == "":
            return None
        value = value.strip()
        if not _EMAIL_RE.match(value):
            raise ValueError("Enter a valid email address.")
        return value

    @field_validator("phone", "address")
    @classmethod
    def blank_to_none(cls, value: str | None) -> str | None:
        if value is None or value.strip() == "":
            return None
        return value.strip()
