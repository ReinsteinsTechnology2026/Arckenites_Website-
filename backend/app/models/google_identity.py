from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class UserGoogleIdentity(Base):
    """The Google account linked to one existing portal user. Matched on the
    stable Google `sub`, never on the email address alone. One portal account
    links to at most one Google account, and one Google account to one portal
    account (both enforced by unique constraints)."""

    __tablename__ = "user_google_identities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True, nullable=False)
    google_sub: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    # The verified Google email at the time of linking. Informational; sign-in
    # still requires the current portal email to match.
    linked_email: Mapped[str] = mapped_column(String(255), nullable=False)
    linked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class GoogleLoginTicket(Base):
    """A one-time ticket handed from the Google callback to the login page.
    Only a SHA-256 hash of the ticket is stored. It is single-use (used_at)
    and short-lived (expires_at)."""

    __tablename__ = "google_login_tickets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
