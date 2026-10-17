import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.models.student import PROGRAM_LABELS, ProgramEnum

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

PROGRAM_CHOICES: list[dict[str, str]] = [
    {"value": p.value, "label": label} for p, label in PROGRAM_LABELS.items()
]


class CreateStudentRequest(BaseModel):
    full_name: str = Field(min_length=1, max_length=200)
    email: str = Field(min_length=3, max_length=255)
    temp_password: str = Field(min_length=8, max_length=128, repr=False)
    program_id: int = Field(gt=0)

    @field_validator("full_name", mode="before")
    @classmethod
    def strip_name(cls, value: str) -> str:
        return value.strip() if isinstance(value, str) else value

    @field_validator("email", mode="before")
    @classmethod
    def validate_email(cls, value: str) -> str:
        if not isinstance(value, str):
            return value
        value = value.strip().lower()
        # Same unquoted-email convention as the existing portal, with domain
        # and local-part checks so malformed addresses never reach the provider.
        if not _EMAIL_RE.fullmatch(value) or any(c in value for c in '<>"(),;:\\[]'):
            raise ValueError("Please enter a valid email address.")
        local, domain = value.rsplit("@", 1)
        labels = domain.split(".")
        if (len(local) > 64 or local.startswith(".") or local.endswith(".") or ".." in local
                or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in labels)):
            raise ValueError("Please enter a valid email address.")
        return value

    @field_validator("temp_password")
    @classmethod
    def validate_password_bytes(cls, value: str) -> str:
        # bcrypt 4.x silently truncates after 72 bytes. Reject that input
        # for new credentials instead of issuing an ambiguous password.
        if len(value.encode("utf-8")) > 72:
            raise ValueError("Temporary password must be at most 72 UTF-8 bytes.")
        return value


class ResendStudentCredentialsRequest(BaseModel):
    temp_password: str = Field(min_length=8, max_length=128, repr=False)

    @field_validator("temp_password")
    @classmethod
    def validate_password_bytes(cls, value: str) -> str:
        return CreateStudentRequest.validate_password_bytes(value)


class StudentProgramOption(BaseModel):
    id: int
    name: str
    slug: str

    model_config = {"from_attributes": True}


class StudentAdminOut(BaseModel):
    id: int
    username: str
    full_name: str
    photo_url: str | None = None
    program: str | None
    program_id: int | None = None
    program_name: str | None = None
    phone: str | None = None
    email: str | None = None
    is_active: bool
    must_change_password: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class CredentialEmailOut(BaseModel):
    credential_email_sent: bool
    detail: str


class StudentCreatedOut(StudentAdminOut, CredentialEmailOut):
    pass


class DeleteStudentResponse(BaseModel):
    """Result of DELETE /admin/students/{id}. A student with any related
    records (enrollments, support tickets, lab access, batch chat, meetings,
    etc.) is deactivated instead of hard-deleted — see routes_admin_students
    .delete_student for why. `student` is populated only when the account
    was deactivated (there is nothing left to return after a real delete)."""
    action: Literal["deleted", "deactivated"]
    detail: str
    student: StudentAdminOut | None = None


def _validate_program_value(value: str) -> str:
    if value not in ProgramEnum._value2member_map_:
        allowed = ", ".join(p.value for p in ProgramEnum)
        raise ValueError(f"program must be one of: {allowed}")
    return value


class CompleteProfileRequest(BaseModel):
    """The one-time "tell us about yourself" step every student fills in
    right after setting their own password, before reaching the dashboard.
    Just name/mobile/email — current_role was dropped from this step."""
    full_name: str = Field(min_length=1, max_length=200)
    mobile_number: str = Field(min_length=7, max_length=30)
    email: str = Field(min_length=3, max_length=255)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        if not _EMAIL_RE.match(value):
            raise ValueError("Enter a valid email address.")
        return value


class UpdateStudentRequest(BaseModel):
    """All fields optional — admin edits are partial updates, PATCH-style.
    reset_temp_password (if given) re-arms must_change_password so the
    student is forced through the set-new-password flow again, same as a
    freshly created account."""
    full_name: str | None = Field(default=None, min_length=1, max_length=200)
    is_active: bool | None = None
    program: str | None = None
    program_id: int | None = Field(default=None, gt=0)
    reset_temp_password: str | None = Field(default=None, min_length=8, max_length=128)

    @field_validator("program")
    @classmethod
    def validate_program(cls, value: str | None) -> str | None:
        return value if value is None else _validate_program_value(value)
