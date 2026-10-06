import re
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

# Same pattern as CreateContactEnquiryRequest (schemas/contact_enquiry.py) —
# this codebase validates email format with a plain regex on a str field
# rather than pydantic's EmailStr, which would pull in a new dependency
# (email-validator) not currently installed anywhere in this project.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_MOBILE_ALLOWED_RE = re.compile(r"^\+?[0-9 ()\-.]+$")


def _validate_email_str(value: str) -> str:
    value = value.strip().lower()
    if not _EMAIL_RE.match(value):
        raise ValueError("Enter a valid email address.")
    return value


# A generic response used for every step of the public registration flow
# where the specific outcome (email already registered, registration not
# found, etc.) must never be distinguishable from the success case — see
# routes_community_auth.py's account-enumeration notes.
GENERIC_REGISTRATION_MESSAGE = "If the information can be used for registration, a verification email will be sent."


class GenericMessageOut(BaseModel):
    detail: str


class StartCommunityRegistrationRequest(BaseModel):
    full_name: str = Field(min_length=2, max_length=200)
    mobile_number: str = Field(min_length=7, max_length=20)
    email: str = Field(min_length=3, max_length=255)
    # True only when the user explicitly chooses "Start Again" on an
    # in-progress registration. Otherwise a repeated submit is not a restart.
    restart: bool = False

    @field_validator("full_name")
    @classmethod
    def _strip_name(cls, v: str) -> str:
        return v.strip()

    @field_validator("mobile_number")
    @classmethod
    def _validate_mobile(cls, v: str) -> str:
        # Spaces, dashes, dots and brackets are allowed as separators; a leading
        # "+" is allowed. The stored display value keeps what the user typed;
        # comparisons use core/community_identity.normalize_mobile.
        raw = v.strip()
        if not _MOBILE_ALLOWED_RE.match(raw):
            raise ValueError("Enter a valid mobile number.")
        digit_count = len(re.sub(r"\D", "", raw))
        if not (7 <= digit_count <= 15):
            raise ValueError("Enter a valid mobile number.")
        return raw

    @field_validator("email")
    @classmethod
    def _validate_email(cls, v: str) -> str:
        return _validate_email_str(v)


class VerifyCommunityOtpRequest(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    otp: str = Field(min_length=6, max_length=6, pattern=r"^\d{6}$")

    @field_validator("email")
    @classmethod
    def _validate_email(cls, v: str) -> str:
        return _validate_email_str(v)


class VerifyCommunityOtpResponse(BaseModel):
    verification_token: str
    expires_in_minutes: int


class ResendCommunityOtpRequest(BaseModel):
    email: str = Field(min_length=3, max_length=255)

    @field_validator("email")
    @classmethod
    def _validate_email(cls, v: str) -> str:
        return _validate_email_str(v)


class SetCommunityPasswordRequest(BaseModel):
    verification_token: str = Field(min_length=10)
    new_password: str = Field(min_length=1, max_length=200)
    confirm_password: str = Field(min_length=1, max_length=200)


class UpdateCommunityMemberRequest(BaseModel):
    is_active: bool


class CommunityMemberOut(BaseModel):
    """Super Admin's Community Database row. Deliberately excludes
    password_hash, any OTP field, and any token — these never leave the
    backend, not even to a Super Admin."""
    id: int
    full_name: str
    mobile_number: str | None
    email: str | None
    email_verified: bool
    is_active: bool
    created_at: datetime
