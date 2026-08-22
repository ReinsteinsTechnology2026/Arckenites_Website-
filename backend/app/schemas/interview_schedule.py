from datetime import date, datetime, time

from pydantic import BaseModel, Field, field_validator

from app.models.interview_schedule import InterviewModeEnum, InterviewStatusEnum


def _validate_choice(value: str, enum_cls, field_name: str) -> str:
    if value not in enum_cls._value2member_map_:
        allowed = ", ".join(e.value for e in enum_cls)
        raise ValueError(f"{field_name} must be one of: {allowed}")
    return value


class CreateInterviewRequest(BaseModel):
    student_id: int
    company_name: str = Field(min_length=1, max_length=200)
    role: str | None = Field(default=None, max_length=200)
    interview_date: date
    interview_time: time | None = None
    mode: str = InterviewModeEnum.online.value
    location_or_link: str | None = Field(default=None, max_length=500)
    notes: str | None = None

    @field_validator("mode")
    @classmethod
    def validate_mode(cls, value: str) -> str:
        return _validate_choice(value, InterviewModeEnum, "mode")


class UpdateInterviewRequest(BaseModel):
    company_name: str | None = Field(default=None, min_length=1, max_length=200)
    role: str | None = Field(default=None, max_length=200)
    interview_date: date | None = None
    interview_time: time | None = None
    mode: str | None = None
    location_or_link: str | None = Field(default=None, max_length=500)
    status: str | None = None
    notes: str | None = None

    @field_validator("mode")
    @classmethod
    def validate_mode(cls, value: str | None) -> str | None:
        return value if value is None else _validate_choice(value, InterviewModeEnum, "mode")

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: str | None) -> str | None:
        return value if value is None else _validate_choice(value, InterviewStatusEnum, "status")


class InterviewOut(BaseModel):
    id: int
    student_id: int
    student_name: str
    student_username: str
    company_name: str
    role: str | None
    interview_date: date
    interview_time: time | None
    mode: str
    location_or_link: str | None
    status: str
    notes: str | None
    created_at: datetime
