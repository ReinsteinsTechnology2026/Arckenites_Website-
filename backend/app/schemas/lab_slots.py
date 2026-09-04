from datetime import date, datetime, time

from pydantic import BaseModel


class LabSlotOut(BaseModel):
    slot_date: date
    start_time: time
    end_time: time
    capacity: int
    booked_count: int
    is_booked_by_me: bool
    is_full: bool
    is_past: bool


class LabWeekSummaryOut(BaseModel):
    week_start: date
    week_end: date
    hours_booked: int
    hours_remaining: int
    weekly_cap: int


class LabBookingOut(BaseModel):
    id: int
    slot_date: date
    start_time: time
    end_time: time
    created_at: datetime

    model_config = {"from_attributes": True}


class LabSlotsPageOut(BaseModel):
    slots: list[LabSlotOut]
    week_summary: LabWeekSummaryOut
    my_bookings: list[LabBookingOut]


class LabSlotBookRequest(BaseModel):
    slot_date: date
    start_time: time
