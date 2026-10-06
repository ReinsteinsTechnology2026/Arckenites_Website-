from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.community_post import (
    CommunityPostEmailStatus,
    CommunityPostKind,
    CommunityPostStatus,
    CommunityPostVisibility,
)


def _single_line(value: str) -> str:
    # Titles appear in email subjects, so they must never contain line breaks.
    cleaned = value.strip()
    if "\n" in cleaned or "\r" in cleaned:
        raise ValueError("Title must be a single line.")
    if not cleaned:
        raise ValueError("Title is required.")
    return cleaned


class CommunityPostCreate(BaseModel):
    kind: CommunityPostKind
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=5000)
    visibility: CommunityPostVisibility = CommunityPostVisibility.public

    @field_validator("title")
    @classmethod
    def _check_title(cls, v: str) -> str:
        return _single_line(v)


class CommunityPostUpdate(BaseModel):
    # Every field optional: PATCH only changes what is sent. Editing a
    # published post updates the portal content but never sends email.
    kind: CommunityPostKind | None = None
    title: str | None = Field(default=None, min_length=1, max_length=200)
    body: str | None = Field(default=None, min_length=1, max_length=5000)
    visibility: CommunityPostVisibility | None = None

    @field_validator("title")
    @classmethod
    def _check_title(cls, v: str | None) -> str | None:
        return None if v is None else _single_line(v)


class PublicPostOut(BaseModel):
    """What anyone (including logged-out visitors) may see. No author, no
    visibility, no delivery information."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    kind: CommunityPostKind
    title: str
    body: str
    published_at: datetime | None


class MemberPostOut(PublicPostOut):
    """Member feed item: the public fields plus visibility, so the page can
    label members-only content. Never returned by the public endpoint."""

    visibility: CommunityPostVisibility


class AdminPostOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    kind: CommunityPostKind
    title: str
    body: str
    visibility: CommunityPostVisibility
    status: CommunityPostStatus
    published_at: datetime | None
    created_at: datetime
    updated_at: datetime
    # Rolled-up delivery state: No recipients / Pending / Sent / Partial / Failed.
    # Null for drafts, which have not been sent anything.
    email_status: str | None = None
    queued_recipient_count: int = 0


class DeliveryOut(BaseModel):
    """Admin-only delivery row. Carries the recipient's user id and a status,
    never their email address."""

    user_id: int
    status: CommunityPostEmailStatus
    attempts: int = 0
    sent_at: datetime | None
    error_code: str | None
