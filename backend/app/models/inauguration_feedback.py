from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class InaugurationFeedback(Base):
    """Feedback submitted from the public Inauguration Session feedback page."""

    __tablename__ = "inauguration_feedback"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    domain: Mapped[str] = mapped_column(String(50), nullable=False)
    domain_other: Mapped[str | None] = mapped_column(String(200), nullable=True)
    overall_session: Mapped[str] = mapped_column(String(30), nullable=False)
    usefulness: Mapped[str] = mapped_column(String(30), nullable=False)
    learned_something: Mapped[str] = mapped_column(String(10), nullable=False)
    liked_topic: Mapped[str] = mapped_column(String(30), nullable=False)
    liked_topic_other: Mapped[str | None] = mapped_column(String(200), nullable=True)
    attend_future: Mapped[str] = mapped_column(String(10), nullable=False)
    suggestion: Mapped[str | None] = mapped_column(Text, nullable=True)
    contact_preference: Mapped[str] = mapped_column(String(10), nullable=False)
    contact_number: Mapped[str | None] = mapped_column(String(20), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
