from datetime import datetime

from pydantic import BaseModel, Field


class UserSearchResult(BaseModel):
    id: int
    username: str
    full_name: str
    role: str


class MessageOut(BaseModel):
    id: int
    sender_id: int
    sender_name: str
    is_me: bool
    body: str
    created_at: datetime


class ConversationOut(BaseModel):
    other_user_id: int
    other_username: str
    other_name: str
    other_role: str
    last_message: str | None
    last_message_at: datetime | None
    unread_count: int
    is_pinned_admin: bool = False


class SendMessageBody(BaseModel):
    body: str = Field(min_length=1, max_length=2000)
