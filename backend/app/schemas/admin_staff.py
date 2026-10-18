import re
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.core.security import validate_bcrypt_password_bytes
from app.core.email_identity import normalize_login_email
from app.models.staff import PERMISSION_KEYS
from app.schemas.admin_students import CredentialEmailOut, ResendStudentCredentialsRequest

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _clean_permissions(value: dict | None) -> dict:
    """Silently drops any key outside the fixed PERMISSION_KEYS allow-list —
    keeps the persisted shape locked to permissions the UI actually offers."""
    if not value:
        return {}
    return {k: bool(v) for k, v in value.items() if k in PERMISSION_KEYS}


class CreateStaffRequest(BaseModel):
    """New trainers sign in with their normalized, administrator-supplied email."""
    full_name: str = Field(min_length=1, max_length=200)
    email: str = Field(min_length=3, max_length=255)
    temp_password: str = Field(min_length=8, max_length=128)

    @field_validator("full_name", mode="before")
    @classmethod
    def strip_name(cls, value: str) -> str:
        return value.strip() if isinstance(value, str) else value

    @field_validator("temp_password")
    @classmethod
    def check_password_bytes(cls, value: str) -> str:
        return validate_bcrypt_password_bytes(value)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        return normalize_login_email(value)


class UpdateStaffRequest(BaseModel):
    """All fields optional — admin edits are partial updates, PATCH-style,
    same convention as UpdateStudentRequest."""
    full_name: str | None = Field(default=None, min_length=1, max_length=200)
    email: str | None = None
    is_active: bool | None = None
    reset_temp_password: str | None = Field(default=None, min_length=8, max_length=128)
    permissions: dict[str, bool] | None = None

    @field_validator("reset_temp_password")
    @classmethod
    def check_password_bytes(cls, value: str | None) -> str | None:
        return validate_bcrypt_password_bytes(value) if value is not None else None

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
    photo_url: str | None = None
    email: str | None = None
    is_active: bool
    must_change_password: bool
    last_login_at: datetime | None = None
    created_at: datetime
    permissions: dict[str, bool] = Field(default_factory=dict)

    model_config = {"from_attributes": True}


class StaffCreatedOut(StaffAdminOut, CredentialEmailOut):
    pass


class ResendStaffCredentialsRequest(ResendStudentCredentialsRequest):
    pass
