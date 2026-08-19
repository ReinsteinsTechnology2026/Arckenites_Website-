import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class AuthEventType(str, enum.Enum):
    login_success = "login_success"
    login_failure = "login_failure"
    logout = "logout"
    password_change = "password_change"
    account_locked = "account_locked"
    rate_limited = "rate_limited"


class AuthAuditLog(Base):
    __tablename__ = "auth_audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # nullable: a login attempt against a username that doesn't exist has no user_id,
    # but we still want to record the attempt for security review.
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    username_attempted: Mapped[str | None] = mapped_column(String(120), nullable=True)

    event_type: Mapped[AuthEventType] = mapped_column(Enum(AuthEventType, name="auth_event_type"), nullable=False)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
