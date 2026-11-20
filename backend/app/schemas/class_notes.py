from datetime import datetime

from pydantic import BaseModel, Field

MAX_NOTE_CHARS = 20000


class PrivateNoteIn(BaseModel):
    content: str = Field(max_length=MAX_NOTE_CHARS)


class PrivateNoteOut(BaseModel):
    run_id: int
    content: str
    updated_at: datetime | None = None
