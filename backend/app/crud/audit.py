from sqlalchemy.orm import Session

from app.models.audit_log import AuthAuditLog, AuthEventType
from app.models.user import User


def write_audit_event(
    db: Session,
    event_type: AuthEventType,
    ip_address: str | None,
    user_agent: str | None,
    user: User | None = None,
    username_attempted: str | None = None,
    detail: str | None = None,
) -> None:
    entry = AuthAuditLog(
        user_id=user.id if user else None,
        username_attempted=username_attempted or (user.username if user else None),
        event_type=event_type,
        ip_address=ip_address,
        user_agent=user_agent,
        detail=detail,
    )
    db.add(entry)
    db.commit()
