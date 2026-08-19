import re
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.models.staff import PERMISSION_KEYS

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_USERNAME_RE = re.compile(r"^[a-zA-Z0-9._@-]{3,120}$")


def _clean_permissions(value: dict | None) -> dict:
    """Silently drops any key outside the fixed PERMISSION_KEYS allow-list —
    keeps the persisted shape locked to permissions the UI actually offers."""
    if not value:
        return {}
    return {k: bool(v) for k, v in value.items() if k in PERMISSION_KEYS}


class CreateStaffRequest(BaseModel):
    full_name: str = Field(min_length=1, max_length=200)
    username: str
    email: str | None = None
    temp_password: str = Field(min_length=8, max_length=128)

    @field_validator("username")
    @classmethod
    def validate_username(cls, value: str) -> str:
        value = value.strip()
        if not _USERNAME_RE.match(value):
            raise ValueError("Username must be 3-120 characters (letters, numbers, . _ @ - only).")
        return value

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str | None) -> str | None:
        if value is None or value.strip() == "":
            return None
        value = value.strip()
        if not _EMAIL_RE.match(value):
            raise ValueError("Enter a valid email address.")
        return value


class UpdateStaffRequest(BaseModel):
    """All fields optional — admin edits are partial updates, PATCH-style,
    same convention as UpdateStudentRequest."""
    full_name: str | None = Field(default=None, min_length=1, max_length=200)
    email: str | None = None
    is_active: bool | None = None
    reset_temp_password: str | None = Field(default=None, min_length=8, max_length=128)
    permissions: dict[str, bool] | None = None

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str | None) -> str | None:
        if value is None or value.strip() == "":
            return value
        value = value.strip()
        if not _EMAIL_RE.match(value):
            raise ValueError("Enter a valid email address.")
        return value


class StaffAdminOut(BaseModel):
    id: int
    username: str
    full_name: str
    email: str | None = None
    is_active: bool
    must_change_password: bool
    last_login_at: datetime | None = None
    created_at: datetime
    permissions: dict[str, bool] = Field(default_factory=dict)

    model_config = {"from_attributes": True}
