from datetime import datetime

from pydantic import BaseModel, Field


class UserSearchResult(BaseModel):
    """Cross-role directory entry shown to any authenticated user — public
    identity only (no username: that's private, per the profile visibility
    rules). `role` is kept since it's not contact/account info and the UI
    groups results by student/trainer/admin."""
    id: int
    full_name: str
    role: str
    photo_url: str | None = None


class MessageOut(BaseModel):
    id: int
    sender_id: int
    sender_name: str
    sender_photo_url: str | None = None
    is_me: bool
    body: str
    created_at: datetime


class ConversationOut(BaseModel):
    other_user_id: int
    other_name: str
    other_role: str
    other_photo_url: str | None = None
    last_message: str | None
    last_message_at: datetime | None
    unread_count: int
    is_pinned_admin: bool = False


class SendMessageBody(BaseModel):
    body: str = Field(min_length=1, max_length=2000)
