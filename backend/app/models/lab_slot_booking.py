from datetime import date as date_, datetime, time as time_

from sqlalchemy import Date, DateTime, ForeignKey, Integer, Time, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class LabSlotBooking(Base):
    """A student's booking of one fixed 2-hour lab slot on a given date.
    Slot times themselves are a fixed daily template (app/core/lab_slots.py),
    not admin-configurable yet — real capacity/setup to be defined later."""

    __tablename__ = "lab_slot_bookings"
    __table_args__ = (
        UniqueConstraint("student_id", "slot_date", "start_time", name="uq_lab_booking_student_slot"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)

    slot_date: Mapped[date_] = mapped_column(Date, nullable=False, index=True)
    start_time: Mapped[time_] = mapped_column(Time, nullable=False)
    end_time: Mapped[time_] = mapped_column(Time, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
