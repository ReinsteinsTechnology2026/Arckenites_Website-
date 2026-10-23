from datetime import date, datetime, time

from pydantic import BaseModel, Field, field_validator, model_validator

from app.models.trainer_scheduling import AVAILABILITY_KINDS, ENTRY_CATEGORIES


# ------------------------------------------------------------------ requests

class RuleCreate(BaseModel):
    """Weekly recurring window, one row is created per weekday (0 = Monday)."""
    weekdays: list[int] = Field(min_length=1, max_length=7)
    start_time: time
    end_time: time
    kind: str = "available"
    effective_from: date
    effective_until: date | None = None

    @field_validator("weekdays")
    @classmethod
    def _weekdays(cls, v: list[int]) -> list[int]:
        if any(d < 0 or d > 6 for d in v):
            raise ValueError("weekdays must be 0 (Monday) to 6 (Sunday)")
        return sorted(set(v))

    @field_validator("kind")
    @classmethod
    def _kind(cls, v: str) -> str:
        if v not in ("available", "preferred"):
            raise ValueError("kind must be 'available' or 'preferred'")
        return v

    @model_validator(mode="after")
    def _order(self):
        if self.end_time <= self.start_time:
            raise ValueError("end_time must be after start_time")
        if self.effective_until and self.effective_until < self.effective_from:
            raise ValueError("effective_until must not be before effective_from")
        return self


class RuleUpdate(BaseModel):
    start_time: time | None = None
    end_time: time | None = None
    kind: str | None = None
    effective_from: date | None = None
    effective_until: date | None = None
    clear_effective_until: bool = False

    @field_validator("kind")
    @classmethod
    def _kind(cls, v: str | None) -> str | None:
        if v is not None and v not in ("available", "preferred"):
            raise ValueError("kind must be 'available' or 'preferred'")
        return v


class ReviewOptions(BaseModel):
    """Used when a change would uncover a confirmed batch: the trainer cannot
    apply it directly, but can submit it to administrators for review."""
    submit_for_review: bool = False
    review_note: str | None = Field(default=None, max_length=500)


class EntryCreate(ReviewOptions):
    kind: str
    category: str | None = None
    start_date: date
    start_time: time
    end_date: date | None = None  # defaults to start_date
    end_time: time
    note: str | None = Field(default=None, max_length=300)

    @field_validator("kind")
    @classmethod
    def _kind(cls, v: str) -> str:
        if v not in AVAILABILITY_KINDS:
            raise ValueError(f"kind must be one of: {', '.join(AVAILABILITY_KINDS)}")
        return v

    @field_validator("category")
    @classmethod
    def _category(cls, v: str | None) -> str | None:
        if v is not None and v not in ENTRY_CATEGORIES:
            raise ValueError(f"category must be one of: {', '.join(ENTRY_CATEGORIES)}")
        return v


class EntryUpdate(ReviewOptions):
    kind: str | None = None
    category: str | None = None
    start_date: date | None = None
    start_time: time | None = None
    end_date: date | None = None
    end_time: time | None = None
    note: str | None = Field(default=None, max_length=300)

    @field_validator("kind")
    @classmethod
    def _kind(cls, v: str | None) -> str | None:
        if v is not None and v not in AVAILABILITY_KINDS:
            raise ValueError(f"kind must be one of: {', '.join(AVAILABILITY_KINDS)}")
        return v

    @field_validator("category")
    @classmethod
    def _category(cls, v: str | None) -> str | None:
        if v is not None and v not in ENTRY_CATEGORIES:
            raise ValueError(f"category must be one of: {', '.join(ENTRY_CATEGORIES)}")
        return v


class AssignRequest(BaseModel):
    trainer_id: int | None = None  # null = remove the current trainer
    idempotency_key: str = Field(min_length=8, max_length=64)
    override: bool = False
    reason: str | None = Field(default=None, max_length=1000)


class ExpertiseUpdate(BaseModel):
    program_ids: list[int] = Field(max_length=100)


class AvailabilityRequestCreate(BaseModel):
    message: str = Field(min_length=3, max_length=1000)
    start_date: date
    end_date: date | None = None


# ----------------------------------------------------------------- responses

class RuleOut(BaseModel):
    id: int
    weekday: int
    start_time: str
    end_time: str
    kind: str
    effective_from: date
    effective_until: date | None


class EntryOut(BaseModel):
    id: int
    kind: str
    category: str | None
    note: str | None
    start_date: date
    start_time: str
    end_date: date
    end_time: str
    starts_at: datetime
    ends_at: datetime


class Segment(BaseModel):
    date: date
    start_time: str
    end_time: str
    state: str  # available | preferred | unavailable | leave | booked | requested
    label: str | None = None
    ref_id: int | None = None  # entry id / batch id / notice id


class CalendarSummary(BaseModel):
    available_hours: float
    booked_hours: float
    free_hours: float
    preferred_hours: float
    upcoming_sessions: list[dict]


class CalendarOut(BaseModel):
    trainer_id: int
    trainer_name: str
    timezone: str
    start: date
    end: date
    segments: list[Segment]
    rules: list[RuleOut]
    entries: list[EntryOut]
    summary: CalendarSummary


class NoticeOut(BaseModel):
    id: int
    kind: str
    message: str
    trainer_id: int | None
    trainer_name: str | None = None
    batch_id: int | None
    batch_name: str | None = None
    starts_at: datetime | None
    ends_at: datetime | None
    status: str
    created_at: datetime


class TrainerRow(BaseModel):
    id: int
    full_name: str
    photo_url: str | None
    programs: list[dict]
    batch_count: int
    assigned_hours: float
    available_hours: float
    free_hours: float
    has_availability: bool
    slot_status: str | None = None  # free | booked | unavailable | not_declared (when a slot filter is given)


class TrainerListOut(BaseModel):
    items: list[TrainerRow]
    total: int
    page: int
    page_size: int
    period_start: date
    period_end: date
    timezone: str


class BatchRow(BaseModel):
    id: int
    name: str
    status: str
    program_id: int | None
    program_name: str | None
    class_days: list[str]
    start_time: str | None
    end_time: str | None
    start_date: date | None
    end_date: date | None
    trainer_id: int | None
    trainer_name: str | None


class BatchListOut(BaseModel):
    items: list[BatchRow]
    total: int
    page: int
    page_size: int


class AssignmentHistoryOut(BaseModel):
    id: int
    batch_id: int
    batch_name: str | None
    action: str
    from_trainer: str | None
    to_trainer: str | None
    actor: str | None
    was_override: bool
    reason: str | None
    overridden_conflicts: list | None
    created_at: datetime


class HistoryListOut(BaseModel):
    items: list[AssignmentHistoryOut]
    total: int
    page: int
    page_size: int


class AssignResult(BaseModel):
    batch_id: int
    trainer_id: int | None
    trainer_name: str | None
    action: str
    was_override: bool
    replayed: bool = False
    warnings: list[str] = []
