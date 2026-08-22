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
    session_revoked = "session_revoked"

    student_created = "student_created"
    student_updated = "student_updated"
    student_enabled = "student_enabled"
    student_disabled = "student_disabled"
    student_deleted = "student_deleted"
    student_password_reset = "student_password_reset"

    trainer_created = "trainer_created"
    trainer_updated = "trainer_updated"
    trainer_enabled = "trainer_enabled"
    trainer_disabled = "trainer_disabled"
    trainer_deleted = "trainer_deleted"
    trainer_password_reset = "trainer_password_reset"
    trainer_permissions_changed = "trainer_permissions_changed"

    program_created = "program_created"
    program_updated = "program_updated"
    program_deleted = "program_deleted"

    batch_created = "batch_created"
    batch_updated = "batch_updated"
    batch_deleted = "batch_deleted"

    admin_user_created = "admin_user_created"
    admin_user_updated = "admin_user_updated"
    admin_user_enabled = "admin_user_enabled"
    admin_user_disabled = "admin_user_disabled"
    admin_user_deleted = "admin_user_deleted"
    admin_user_password_reset = "admin_user_password_reset"
    admin_user_role_changed = "admin_user_role_changed"

    role_created = "role_created"
    role_updated = "role_updated"
    role_deleted = "role_deleted"
    permission_changed = "permission_changed"

    settings_changed = "settings_changed"

    support_ticket_created = "support_ticket_created"
    support_ticket_replied = "support_ticket_replied"
    support_ticket_assigned = "support_ticket_assigned"
    support_ticket_status_changed = "support_ticket_status_changed"
    support_ticket_priority_changed = "support_ticket_priority_changed"
    support_ticket_internal_note_added = "support_ticket_internal_note_added"
    support_ticket_closed = "support_ticket_closed"
    support_ticket_reopened = "support_ticket_reopened"
    support_ticket_deleted = "support_ticket_deleted"


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

    # Added for the Activity Logs feature — all nullable so existing auth-only
    # rows (and existing write_audit_event call sites) stay valid untouched.
    module: Mapped[str | None] = mapped_column(String(50), nullable=True)
    target: Mapped[str | None] = mapped_column(String(200), nullable=True)
    status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # Snapshot of the actor's role/admin-role-slug at write time, since a
    # user's role can change later and the log should reflect what it was
    # when the action happened, not what it is now.
    actor_role: Mapped[str | None] = mapped_column(String(50), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
