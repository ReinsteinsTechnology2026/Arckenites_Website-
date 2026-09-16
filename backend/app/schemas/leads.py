from datetime import datetime

from pydantic import BaseModel, Field, field_validator

LEAD_STATUSES: list[str] = ["new", "contacted", "qualified", "converted", "lost"]


def _validate_status(value: str) -> str:
    if value not in LEAD_STATUSES:
        raise ValueError(f"status must be one of: {', '.join(LEAD_STATUSES)}")
    return value


class CreateLeadRequest(BaseModel):
    full_name: str = Field(min_length=1, max_length=200)
    phone: str | None = Field(default=None, max_length=30)
    email: str | None = Field(default=None, max_length=255)
    source: str | None = Field(default=None, max_length=100)
    notes: str | None = None


class UpdateLeadRequest(BaseModel):
    """All fields optional — partial updates, PATCH-style, same convention as
    the other admin edit endpoints."""
    full_name: str | None = Field(default=None, min_length=1, max_length=200)
    phone: str | None = None
    email: str | None = None
    source: str | None = Field(default=None, max_length=100)
    status: str | None = None
    notes: str | None = None

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: str | None) -> str | None:
        return value if value is None else _validate_status(value)


class LeadOut(BaseModel):
    id: int
    full_name: str
    phone: str | None = None
    email: str | None = None
    source: str | None = None
    status: str
    notes: str | None = None
    created_by_name: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
