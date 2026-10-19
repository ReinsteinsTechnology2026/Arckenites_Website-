from datetime import date, datetime
import ipaddress
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.job_application import JobApplicationStatus, JobReactionKind


def _text(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("This field cannot be empty")
    return value


def _url(value: str) -> str:
    value = value.strip()
    if any(ord(char) < 33 or ord(char) == 127 or char == "\\" for char in value):
        raise ValueError("Enter a valid HTTP or HTTPS URL")
    try:
        parsed = urlsplit(value)
        host = parsed.hostname or ""
        valid = parsed.scheme.lower() in {"http", "https"} and host and parsed.port != 0
    except ValueError:
        valid = False
    if not valid or parsed.username or parsed.password:
        raise ValueError("Enter a valid HTTP or HTTPS URL without credentials")
    if host in {"localhost", "localhost.localdomain"} or host.endswith((".localhost", ".local", ".internal")):
        raise ValueError("Enter an external application URL")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        if not address.is_global:
            raise ValueError("Enter an external application URL")
    return value


class JobFields(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=200)
    company: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=5000)
    eligibility: str | None = Field(default=None, max_length=3000)
    application_url: str = Field(max_length=2048)
    deadline: date | None = None

    @field_validator("title", "company", "description")
    @classmethod
    def check_text(cls, value: str) -> str:
        return _text(value)

    @field_validator("eligibility")
    @classmethod
    def check_eligibility(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None

    @field_validator("application_url")
    @classmethod
    def check_url(cls, value: str) -> str:
        return _url(value)


class JobCreate(JobFields):
    pass


class JobUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = Field(default=None, min_length=1, max_length=200)
    company: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, min_length=1, max_length=5000)
    eligibility: str | None = Field(default=None, max_length=3000)
    application_url: str | None = Field(default=None, max_length=2048)
    deadline: date | None = None

    @field_validator("title", "company", "description")
    @classmethod
    def check_text(cls, value: str | None) -> str:
        if value is None:
            raise ValueError("This field cannot be empty")
        return _text(value)

    @field_validator("eligibility")
    @classmethod
    def check_eligibility(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None

    @field_validator("application_url")
    @classmethod
    def check_url(cls, value: str | None) -> str:
        if value is None:
            raise ValueError("Application URL is required")
        return _url(value)


class JobReactionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: JobReactionKind


class JobOut(BaseModel):
    id: int
    title: str
    company: str
    description: str
    eligibility: str | None
    application_url: str
    deadline: date | None
    published_at: datetime | None
    reaction_counts: dict[str, int]
    my_reaction: JobReactionKind | None = None


class AdminJobOut(JobOut):
    status: JobApplicationStatus
    is_enabled: bool
    created_at: datetime
    updated_at: datetime
