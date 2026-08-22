from datetime import date, datetime, time

from pydantic import BaseModel, Field


class CreateClassSessionRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    session_date: date
    start_time: time | None = None
    end_time: time | None = None
    meeting_link: str | None = Field(default=None, max_length=500)
    notes: str | None = None


class UpdateClassSessionRequest(BaseModel):
    """All fields optional — admin edits are partial updates, PATCH-style,
    same convention as the rest of the admin API."""
    title: str | None = Field(default=None, min_length=1, max_length=200)
    session_date: date | None = None
    start_time: time | None = None
    end_time: time | None = None
    meeting_link: str | None = Field(default=None, max_length=500)
    notes: str | None = None


class ClassSessionOut(BaseModel):
    id: int
    batch_id: int
    batch_name: str
    title: str
    session_date: date
    start_time: time | None
    end_time: time | None
    meeting_link: str | None
    notes: str | None
    created_at: datetime


class AdminClassSessionOut(ClassSessionOut):
    """Same as ClassSessionOut plus enough context (trainer, program) for the
    admin's cross-batch "every scheduled class" overview — the per-batch
    admin view doesn't need these since the batch page already shows them."""
    trainer_name: str | None
    program_name: str | None
