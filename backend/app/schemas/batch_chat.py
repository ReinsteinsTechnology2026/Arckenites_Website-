from datetime import datetime

from pydantic import BaseModel, Field


class SendBatchMessageRequest(BaseModel):
    message: str = Field(min_length=1)


class BatchMessageOut(BaseModel):
    id: int
    batch_id: int
    sender_id: int
    sender_name: str
    sender_photo_url: str | None = None
    message: str
    created_at: datetime
