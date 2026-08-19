from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.models.program import EnrollmentStatusEnum, ProgramModeEnum, ProgramStatusEnum


def _validate_choice(value: str, enum_cls, field_name: str) -> str:
    if value not in enum_cls._value2member_map_:
        allowed = ", ".join(e.value for e in enum_cls)
        raise ValueError(f"{field_name} must be one of: {allowed}")
    return value


class CreateProgramRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    code: str | None = Field(default=None, max_length=40)
    description: str | None = None
    category: str | None = Field(default=None, max_length=120)
    duration: str | None = Field(default=None, max_length=120)
    eligibility: str | None = None
    objectives: str | None = None
    max_capacity: int | None = Field(default=None, ge=1, le=100000)
    mode: str = ProgramModeEnum.offline.value
    status: str = ProgramStatusEnum.active.value

    @field_validator("mode")
    @classmethod
    def validate_mode(cls, value: str) -> str:
        return _validate_choice(value, ProgramModeEnum, "mode")

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: str) -> str:
        return _validate_choice(value, ProgramStatusEnum, "status")


class UpdateProgramRequest(BaseModel):
    """All fields optional — admin edits are partial updates, PATCH-style,
    same convention as UpdateStudentRequest/UpdateBatchRequest."""
    name: str | None = Field(default=None, min_length=1, max_length=200)
    code: str | None = Field(default=None, max_length=40)
    description: str | None = None
    category: str | None = Field(default=None, max_length=120)
    duration: str | None = Field(default=None, max_length=120)
    eligibility: str | None = None
    objectives: str | None = None
    max_capacity: int | None = Field(default=None, ge=1, le=100000)
    mode: str | None = None
    status: str | None = None

    @field_validator("mode")
    @classmethod
    def validate_mode(cls, value: str | None) -> str | None:
        return value if value is None else _validate_choice(value, ProgramModeEnum, "mode")

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: str | None) -> str | None:
        return value if value is None else _validate_choice(value, ProgramStatusEnum, "status")


class EnrollStudentsRequest(BaseModel):
    student_ids: list[int] = Field(min_length=1)
    status: str = EnrollmentStatusEnum.pending.value

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: str) -> str:
        return _validate_choice(value, EnrollmentStatusEnum, "status")


class UpdateEnrollmentRequest(BaseModel):
    status: str

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: str) -> str:
        return _validate_choice(value, EnrollmentStatusEnum, "status")


class ProgramListItemOut(BaseModel):
    id: int
    slug: str
    name: str
    code: str | None
    description: str | None
    category: str | None
    mode: str
    status: str
    enrolled_count: int
    active_batch_count: int
    created_at: datetime


class ProgramStatsOut(BaseModel):
    total_programs: int
    active_programs: int
    inactive_programs: int
    total_enrolled_students: int
    programs_with_upcoming_batches: int


class EnrollmentStatCounts(BaseModel):
    total: int
    pending: int
    approved: int
    allocated: int
    active: int
    completed: int
    withdrawn: int
    rejected: int


class BatchStatCounts(BaseModel):
    active: int
    upcoming: int
    completed: int


class ProgramBatchSummary(BaseModel):
    id: int
    name: str
    status: str
    trainer_name: str | None
    student_count: int


class CurrentBatchSummary(BaseModel):
    id: int
    name: str


class EnrollmentOut(BaseModel):
    id: int
    student_id: int
    student_name: str
    student_username: str
    status: str
    enrolled_at: datetime
    current_batch: CurrentBatchSummary | None


class ProgramDetailOut(BaseModel):
    id: int
    slug: str
    name: str
    code: str | None
    description: str | None
    category: str | None
    duration: str | None
    eligibility: str | None
    objectives: str | None
    max_capacity: int | None
    mode: str
    status: str
    created_at: datetime

    enrollment_stats: EnrollmentStatCounts
    batch_stats: BatchStatCounts
    batches: list[ProgramBatchSummary]
    enrollments: list[EnrollmentOut]
