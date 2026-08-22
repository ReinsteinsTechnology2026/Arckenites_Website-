import enum
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class TicketStatusEnum(str, enum.Enum):
    open = "open"
    in_progress = "in_progress"
    waiting_for_student = "waiting_for_student"
    resolved = "resolved"
    closed = "closed"


class TicketPriorityEnum(str, enum.Enum):
    low = "low"
    medium = "medium"
    high = "high"


class TicketCategoryEnum(str, enum.Enum):
    account_login = "account_login"
    course_access = "course_access"
    batch = "batch"
    classes_schedule = "classes_schedule"
    assignments = "assignments"
    assessments = "assessments"
    certificates = "certificates"
    payment_fees = "payment_fees"
    technical_issue = "technical_issue"
    trainer_training = "trainer_training"
    other = "other"


class SenderTypeEnum(str, enum.Enum):
    student = "student"
    staff = "staff"
    admin = "admin"


class SupportTicket(Base):
    """A student's or trainer's support/help-desk request. Unread tracking
    mirrors the chat system's model exactly — one last-read timestamp per
    side, not a per-message flag (see Conversation.user_a_last_read_at). The
    admin side uses a single shared timestamp rather than per-admin
    read-state: any admin opening the ticket marks it read for the whole
    team, matching a shared-inbox model rather than needing a join table."""

    __tablename__ = "support_tickets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticket_number: Mapped[str] = mapped_column(String(20), unique=True, nullable=False, index=True)
    requester_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)

    subject: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[TicketCategoryEnum] = mapped_column(Enum(TicketCategoryEnum, name="ticket_category_enum"), nullable=False)
    priority: Mapped[TicketPriorityEnum] = mapped_column(
        Enum(TicketPriorityEnum, name="ticket_priority_enum"), nullable=False, default=TicketPriorityEnum.medium
    )
    status: Mapped[TicketStatusEnum] = mapped_column(
        Enum(TicketStatusEnum, name="ticket_status_enum"), nullable=False, default=TicketStatusEnum.open, index=True
    )

    assigned_to: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)

    requester_last_read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    admin_last_read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    requester: Mapped["User"] = relationship(foreign_keys=[requester_id])
    assignee: Mapped["User | None"] = relationship(foreign_keys=[assigned_to])
    messages: Mapped[list["SupportMessage"]] = relationship(
        back_populates="ticket", cascade="all, delete-orphan", order_by="SupportMessage.created_at"
    )


class SupportMessage(Base):
    """One message in a ticket's thread — a student reply, an admin reply,
    or an admin-only internal note (is_internal=True, never returned by any
    student-facing endpoint)."""

    __tablename__ = "support_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("support_tickets.id"), nullable=False, index=True)
    sender_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    sender_type: Mapped[SenderTypeEnum] = mapped_column(Enum(SenderTypeEnum, name="sender_type_enum"), nullable=False)

    message: Mapped[str] = mapped_column(Text, nullable=False)
    is_internal: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    ticket: Mapped["SupportTicket"] = relationship(back_populates="messages")
    sender: Mapped["User"] = relationship(foreign_keys=[sender_id])
    attachments: Mapped[list["SupportAttachment"]] = relationship(back_populates="message", cascade="all, delete-orphan")


class SupportAttachment(Base):
    """A file attached to one ticket message. Stored on local disk under a
    random UUID filename (storage_reference) — the original file_name is
    display-only and never used as a disk path, to prevent path traversal
    and filename collisions. Never served via a public static mount: every
    download goes through an authenticated endpoint that re-checks ticket
    ownership, same as the rest of the API."""

    __tablename__ = "support_attachments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("support_tickets.id"), nullable=False, index=True)
    message_id: Mapped[int] = mapped_column(ForeignKey("support_messages.id"), nullable=False, index=True)

    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    file_type: Mapped[str] = mapped_column(String(100), nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)
    storage_reference: Mapped[str] = mapped_column(String(255), nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    message: Mapped["SupportMessage"] = relationship(back_populates="attachments")
