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
    module: str | None = None,
    target: str | None = None,
    status: str | None = None,
    actor_role: str | None = None,
) -> None:
    if actor_role is None and user is not None:
        actor_role = user.admin_role.slug if (user.admin_role_id and user.admin_role) else user.role.value

    entry = AuthAuditLog(
        user_id=user.id if user else None,
        username_attempted=username_attempted or (user.username if user else None),
        event_type=event_type,
        ip_address=ip_address,
        user_agent=user_agent,
        detail=detail,
        module=module,
        target=target,
        status=status,
        actor_role=actor_role,
    )
    db.add(entry)
    db.commit()
