import re
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class CreateAdminUserRequest(BaseModel):
    """No username field — auto-generated (AKA@Name), same convention as
    trainer creation. No confirm-password field — a single temp password
    with a Generate button, matching the Trainer form exactly. No email at
    creation either, matching the Trainer form (email is optional/edit-only
    there too, per an earlier explicit decision in this project)."""
    full_name: str = Field(min_length=1, max_length=200)
    admin_role_id: int
    temp_password: str = Field(min_length=8, max_length=128)


class UpdateAdminUserRequest(BaseModel):
    full_name: str | None = Field(default=None, min_length=1, max_length=200)
    email: str | None = None
    admin_role_id: int | None = None
    is_active: bool | None = None
    reset_temp_password: str | None = Field(default=None, min_length=8, max_length=128)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str | None) -> str | None:
        if value is None or value.strip() == "":
            return value
        value = value.strip()
        if not _EMAIL_RE.match(value):
            raise ValueError("Enter a valid email address.")
        return value


class AdminRoleSummary(BaseModel):
    id: int
    name: str
    slug: str

    model_config = {"from_attributes": True}


class AdminUserOut(BaseModel):
    id: int
    username: str
    full_name: str
    email: str | None = None
    admin_role: AdminRoleSummary | None = None
    is_active: bool
    must_change_password: bool
    last_login_at: datetime | None = None
    created_at: datetime

    model_config = {"from_attributes": True}
