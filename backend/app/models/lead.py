import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class LeadStatusEnum(str, enum.Enum):
    new = "new"
    contacted = "contacted"
    qualified = "qualified"
    converted = "converted"
    lost = "lost"


class Lead(Base):
    """A prospective student/customer contact, tracked through a simple
    pipeline (new -> contacted -> qualified -> converted/lost) by whichever
    admin role holds the leads.* permissions — see crud/permissions.py."""

    __tablename__ = "leads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(30), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[LeadStatusEnum] = mapped_column(
        Enum(LeadStatusEnum, name="lead_status_enum"), nullable=False, default=LeadStatusEnum.new, index=True,
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    created_by: Mapped["User"] = relationship(foreign_keys=[created_by_id])
