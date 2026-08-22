from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

SETTINGS_ROW_ID = 1


class SystemSettings(Base):
    """Singleton row (always id=1) — fields are fixed/known, not a generic
    key-value store. Every field here is actually read and enforced
    somewhere (rate_limit.py, routes_auth.py) — nothing decorative."""

    __tablename__ = "system_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # General
    institute_name: Mapped[str] = mapped_column(String(200), nullable=False, default="Arckenites")
    logo_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    contact_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    timezone: Mapped[str] = mapped_column(String(100), nullable=False, default="Asia/Kolkata")
    date_format: Mapped[str] = mapped_column(String(20), nullable=False, default="DD MMM YYYY")

    # Security — enforced in core/rate_limit.py and routes_auth.py
    session_timeout_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    max_login_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    lockout_duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=15)
    require_strong_passwords: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Notifications
    notify_new_account: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    notify_password_reset: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    notify_security_alert: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # System — enforced in routes_auth.py::login
    maintenance_mode: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    updated_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
