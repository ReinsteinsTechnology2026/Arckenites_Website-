import enum
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class CommunityRegistrationStatus(str, enum.Enum):
    pending = "pending"  # OTP sent, not yet verified
    verified = "verified"  # OTP verified, password not yet set


class CommunityRegistration(Base):
    """A community join-request in progress — deliberately NOT a `users`
    row. The existing User table requires a real password_hash and a real
    unique username at creation time, and this codebase's whole account
    model assumes a User row is a real, usable account. Staging the
    unverified/no-password state here instead means: no partially-built
    User row ever exists, "duplicate registration" is a plain lookup
    against this one table, and an abandoned registration (closed browser)
    can never collide with the same email trying again later — it just
    reuses/overwrites this same pending row.

    Deleted the moment it successfully converts into a real
    User + CommunityProfile (routes_community_auth.set_password) — nothing
    sensitive about a completed registration lingers here afterward."""

    __tablename__ = "community_registrations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    mobile_number: Mapped[str] = mapped_column(String(30), nullable=False)
    # Always stored lowercased — every lookup (duplicate check, OTP verify,
    # resend) normalizes to lowercase first, same convention as username
    # uniqueness elsewhere in this codebase.
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)

    status: Mapped[CommunityRegistrationStatus] = mapped_column(
        Enum(CommunityRegistrationStatus, name="community_registration_status_enum"),
        nullable=False,
        default=CommunityRegistrationStatus.pending,
    )

    # OTP state — see app/core/otp.py. Hashed with the same bcrypt
    # implementation as real account passwords (core/security.py); never
    # stored or logged in plaintext, never returned in any API response.
    otp_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    otp_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    otp_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Resend rate limiting: how many OTP sends have happened in the current
    # 15-minute window, and when that window started. Reset once the
    # window has elapsed — see routes_community_auth.py.
    otp_send_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    otp_window_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Issued only after a successful OTP verification — proves to
    # set-password that THIS caller actually completed OTP verification,
    # rather than trusting a client-supplied "emailVerified" flag or a bare
    # email address. Single-use: cleared the instant the account is
    # created. Hashed the same way as the OTP itself.
    verification_token_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    verification_token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class CommunityProfile(Base):
    """Real, completed Community member details — created in the same
    transaction as the User row, the moment password setup succeeds. Same
    "profile table keyed by user_id" shape as StudentProfile/StaffProfile/
    AdminProfile, not a new pattern."""

    __tablename__ = "community_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True, nullable=False)

    mobile_number: Mapped[str] = mapped_column(String(30), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    email_verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    user: Mapped["User"] = relationship(back_populates="community_profile")
