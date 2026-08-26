from datetime import date, datetime, time

from pydantic import BaseModel, Field, field_validator

from app.models.batch import BatchStatusEnum, BatchTypeEnum


def _validate_choice(value: str, enum_cls, field_name: str) -> str:
    if value not in enum_cls._value2member_map_:
        allowed = ", ".join(e.value for e in enum_cls)
        raise ValueError(f"{field_name} must be one of: {allowed}")
    return value


class CreateBatchRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    course: str | None = Field(default=None, max_length=200)
    batch_type: str = BatchTypeEnum.offline.value
    status: str = BatchStatusEnum.upcoming.value
    trainer_id: int | None = None
    program_id: int | None = None
    start_date: date | None = None
    end_date: date | None = None
    max_capacity: int | None = Field(default=None, ge=1, le=1000)
    class_days: list[str] | None = None
    start_time: time | None = None
    end_time: time | None = None
    student_ids: list[int] = Field(default_factory=list)

    @field_validator("batch_type")
    @classmethod
    def validate_batch_type(cls, value: str) -> str:
        return _validate_choice(value, BatchTypeEnum, "batch_type")

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: str) -> str:
        return _validate_choice(value, BatchStatusEnum, "status")


class UpdateBatchRequest(BaseModel):
    """All fields optional — admin edits are partial updates, PATCH-style,
    same convention as UpdateStudentRequest/UpdateStaffRequest."""
    name: str | None = Field(default=None, min_length=1, max_length=200)
    course: str | None = Field(default=None, max_length=200)
    batch_type: str | None = None
    status: str | None = None
    trainer_id: int | None = None
    clear_trainer: bool = False
    program_id: int | None = None
    clear_program: bool = False
    start_date: date | None = None
    end_date: date | None = None
    max_capacity: int | None = Field(default=None, ge=1, le=1000)
    class_days: list[str] | None = None
    start_time: time | None = None
    end_time: time | None = None

    @field_validator("batch_type")
    @classmethod
    def validate_batch_type(cls, value: str | None) -> str | None:
        return value if value is None else _validate_choice(value, BatchTypeEnum, "batch_type")

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: str | None) -> str | None:
        return value if value is None else _validate_choice(value, BatchStatusEnum, "status")


class AllocateStudentsRequest(BaseModel):
    student_ids: list[int] = Field(min_length=1)


class TrainerSummary(BaseModel):
    id: int
    full_name: str
    username: str
    photo_url: str | None = None


class ProgramSummary(BaseModel):
    id: int
    name: str
    slug: str


class StudentSummary(BaseModel):
    id: int
    full_name: str
    username: str
    photo_url: str | None = None
    joined_at: datetime | None = None


class BatchListItemOut(BaseModel):
    id: int
    name: str
    course: str | None
    batch_type: str
    status: str
    trainer: TrainerSummary | None
    program: ProgramSummary | None
    student_count: int
    max_capacity: int | None
    start_date: date | None
    end_date: date | None
    class_days: list[str]
    start_time: time | None
    end_time: time | None


class BatchDetailOut(BatchListItemOut):
    students: list[StudentSummary]


class BatchStatsOut(BaseModel):
    total: int
    active: int
    upcoming: int
    completed: int
