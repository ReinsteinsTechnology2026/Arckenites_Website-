from datetime import date, datetime, time

from pydantic import BaseModel


class LabAccessStateOut(BaseModel):
    status: str          # LOCKED / UNLOCKED
    access_mode: str     # AUTO / MANUAL_UNLOCK / MANUAL_LOCK
    slot_state: str       # ACTIVE / NOT_STARTED / COMPLETED / NO_SLOT
    slot_date: date | None
    slot_start_time: time | None
    slot_end_time: time | None
    updated_at: datetime | None


class AdminLabAccessRowOut(BaseModel):
    student_id: int
    username: str
    full_name: str
    slot_date: date | None
    slot_start_time: time | None
    slot_end_time: time | None
    slot_state: str
    status: str
    access_mode: str
    updated_by_name: str | None
    updated_at: datetime | None


class LabAccessBatchOut(BaseModel):
    id: int
    name: str


class LabAccessActionRequest(BaseModel):
    reason: str | None = None


class LabAccessAuditEntryOut(BaseModel):
    id: int
    admin_name: str
    action: str
    previous_status: str
    new_status: str
    reason: str | None
    created_at: datetime
