import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class CommunityPostKind(str, enum.Enum):
    announcement = "announcement"
    update = "update"


class CommunityPostVisibility(str, enum.Enum):
    # Visible to anyone on the public Community page.
    public = "public"
    # Visible only to authenticated Community members. Never served from a
    # public endpoint.
    members_only = "members_only"


class CommunityPostStatus(str, enum.Enum):
    # Drafts are invisible to everyone except admins and never trigger email.
    draft = "draft"
    published = "published"


class CommunityPostEmailStatus(str, enum.Enum):
    # Lifecycle of one recipient's email for one published post.
    #   pending  -> queued at publish time, not yet attempted
    #   sending  -> claimed by the worker (a crash here is reconciled to failed)
    #   sent     -> Microsoft Graph accepted the message
    #   failed   -> the transport refused or was unreachable; never auto-retried
    #   skipped  -> recipient was no longer eligible when the worker reached them
    pending = "pending"
    sending = "sending"
    sent = "sent"
    failed = "failed"
    skipped = "skipped"


class CommunityPost(Base):
    """An announcement or update shown on the Community page. Created as a
    draft; publishing it is the only action that can trigger email."""

    __tablename__ = "community_posts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[CommunityPostKind] = mapped_column(
        Enum(CommunityPostKind, name="community_post_kind_enum"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    visibility: Mapped[CommunityPostVisibility] = mapped_column(
        Enum(CommunityPostVisibility, name="community_post_visibility_enum"), nullable=False
    )
    status: Mapped[CommunityPostStatus] = mapped_column(
        Enum(CommunityPostStatus, name="community_post_status_enum"),
        nullable=False,
        default=CommunityPostStatus.draft,
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (Index("ix_community_posts_status_published_at", "status", "published_at"),)


class CommunityPostEmailDelivery(Base):
    """One row per (published post, recipient). The unique constraint is the
    duplicate-send guard: publishing again, retrying the request, or
    restarting the server can never create a second row for the same
    recipient, so the same post is never queued to the same person twice.

    Deliberately stores no email address and no message content — only the
    recipient's user id — so the table holds nothing sensitive if it is ever
    read."""

    __tablename__ = "community_post_email_deliveries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    post_id: Mapped[int] = mapped_column(ForeignKey("community_posts.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    status: Mapped[CommunityPostEmailStatus] = mapped_column(
        Enum(CommunityPostEmailStatus, name="community_post_email_status_enum"),
        nullable=False,
        default=CommunityPostEmailStatus.pending,
    )
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Send attempts actually made to the transport (a transport that is not
    # configured does not count). Permanent failure is decided from this.
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    # Earliest time a pending row may be claimed again (exponential backoff,
    # or Graph's Retry-After). NULL means "claim as soon as possible".
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Short machine-readable category only (e.g. "transport_failed",
    # "ineligible", "interrupted") — never a provider message or address.
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("post_id", "user_id", name="uq_community_post_delivery_post_user"),
        Index("ix_community_post_deliveries_status", "status"),
        Index("ix_community_post_deliveries_due", "status", "next_attempt_at"),
    )
