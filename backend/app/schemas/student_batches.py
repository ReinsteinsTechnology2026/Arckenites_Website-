from datetime import date

from pydantic import BaseModel


class StudentBatchCardOut(BaseModel):
    id: int
    name: str
    status: str
    program_name: str | None
    batch_type: str
    start_date: date | None
    unread_chat_count: int
