from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class BatchMessage(Base):
    """One message in a batch's shared group chat — trainer + every enrolled
    student, all in one thread. Deliberately minimal: plain text only, no
    attachments/reactions/edit-message, unlike the 1:1 chat this sits
    alongside."""

    __tablename__ = "batch_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("batches.id"), nullable=False, index=True)
    sender_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    batch: Mapped["Batch"] = relationship(back_populates="chat_messages")
    sender: Mapped["User"] = relationship(foreign_keys=[sender_id])


class BatchChatReadState(Base):
    """Last-read timestamp per (batch, user) — unlike the 1:1 chat's two
    fixed columns (only ever two sides) or the support ticket's single
    shared admin timestamp (any admin reading marks it read for the team),
    a batch has N independent participants, so each one needs their own
    row here to track what they've actually read."""

    __tablename__ = "batch_chat_read_state"
    __table_args__ = (UniqueConstraint("batch_id", "user_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("batches.id"), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    last_read_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
