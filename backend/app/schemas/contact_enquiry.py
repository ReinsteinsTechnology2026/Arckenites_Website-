import re
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class CreateContactEnquiryRequest(BaseModel):
    full_name: str = Field(min_length=1, max_length=200)
    phone: str = Field(min_length=1, max_length=30)
    email: str = Field(min_length=1, max_length=255)
    subject: str | None = Field(default=None, max_length=200)
    message: str = Field(min_length=1)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        value = value.strip()
        if not _EMAIL_RE.match(value):
            raise ValueError("Enter a valid email address.")
        return value

    @field_validator("full_name", "phone", "subject", "message")
    @classmethod
    def strip_str(cls, value: str | None) -> str | None:
        return value.strip() if value else value


class UpdateContactEnquiryRequest(BaseModel):
    is_read: bool


class ContactEnquiryOut(BaseModel):
    id: int
    full_name: str
    phone: str
    email: str
    subject: str | None
    message: str
    is_read: bool
    created_at: datetime
