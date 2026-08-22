from datetime import date

from pydantic import BaseModel


class TrainerBatchCardOut(BaseModel):
    id: int
    name: str
    course: str | None
    batch_type: str
    status: str
    program_name: str | None
    student_count: int
    max_capacity: int | None
    start_date: date | None
    unread_chat_count: int
