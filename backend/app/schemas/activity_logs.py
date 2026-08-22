from datetime import datetime

from pydantic import BaseModel


class ActivityLogEntryOut(BaseModel):
    id: int
    created_at: datetime
    user_id: int | None
    user_name: str | None
    username: str | None
    role: str | None
    event_type: str
    module: str | None
    target: str | None
    status: str | None
    ip_address: str | None
    detail: str | None

    model_config = {"from_attributes": True}


class ActivityLogListOut(BaseModel):
    items: list[ActivityLogEntryOut]
    total: int
    page: int
    page_size: int
