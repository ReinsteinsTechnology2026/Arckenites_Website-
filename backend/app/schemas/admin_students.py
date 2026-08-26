import re
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.models.student import PROGRAM_LABELS, CurrentRoleEnum, ProgramEnum

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

PROGRAM_CHOICES: list[dict[str, str]] = [
    {"value": p.value, "label": label} for p, label in PROGRAM_LABELS.items()
]


class CreateStudentRequest(BaseModel):
    full_name: str = Field(min_length=1, max_length=200)
    temp_password: str = Field(min_length=8, max_length=128)
    program: str | None = None

    @field_validator("program")
    @classmethod
    def validate_program(cls, value: str | None) -> str | None:
        return value if value is None else _validate_program_value(value)


class StudentAdminOut(BaseModel):
    id: int
    username: str
    full_name: str
    photo_url: str | None = None
    program: str | None
    is_active: bool
    must_change_password: bool
    created_at: datetime

    model_config = {"from_attributes": True}


def _validate_program_value(value: str) -> str:
    if value not in ProgramEnum._value2member_map_:
        allowed = ", ".join(p.value for p in ProgramEnum)
        raise ValueError(f"program must be one of: {allowed}")
    return value


class CompleteProfileRequest(BaseModel):
    """The one-time "tell us about yourself" step every student fills in
    right after setting their own password, before reaching the dashboard."""
    full_name: str = Field(min_length=1, max_length=200)
    mobile_number: str = Field(min_length=7, max_length=30)
    email: str = Field(min_length=3, max_length=255)
    current_role: str

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        if not _EMAIL_RE.match(value):
            raise ValueError("Enter a valid email address.")
        return value

    @field_validator("current_role")
    @classmethod
    def validate_current_role(cls, value: str) -> str:
        if value not in CurrentRoleEnum._value2member_map_:
            allowed = ", ".join(r.value for r in CurrentRoleEnum)
            raise ValueError(f"current_role must be one of: {allowed}")
        return value


class UpdateStudentRequest(BaseModel):
    """All fields optional — admin edits are partial updates, PATCH-style.
    reset_temp_password (if given) re-arms must_change_password so the
    student is forced through the set-new-password flow again, same as a
    freshly created account."""
    full_name: str | None = Field(default=None, min_length=1, max_length=200)
    is_active: bool | None = None
    program: str | None = None
    reset_temp_password: str | None = Field(default=None, min_length=8, max_length=128)

    @field_validator("program")
    @classmethod
    def validate_program(cls, value: str | None) -> str | None:
        return value if value is None else _validate_program_value(value)
