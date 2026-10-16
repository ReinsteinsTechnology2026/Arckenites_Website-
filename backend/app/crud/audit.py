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
    commit: bool = True,
) -> None:
    """Stages (and, by default, commits) one audit-log row.

    `commit=True` is the convention every existing caller in this codebase
    relies on: the caller commits its OWN business-logic change first, then
    calls this as a separate, independent follow-up commit for the audit
    trail. That is intentional here and unchanged by this parameter — do not
    flip the default.

    Pass `commit=False` only when the audit entry must be part of the SAME
    atomic transaction as other pending changes on `db` (e.g. a status
    change that must not be considered "done" unless it's also logged, or
    vice versa). The caller is then responsible for calling `db.commit()`
    itself — in one place, after this call, covering every staged change at
    once — and for rolling back on failure. See
    app/api/routes_admin_students.py:delete_student for the motivating case:
    a prior version of that function relied on this function's own commit
    to implicitly flush an unrelated, already-staged `User.is_active` change,
    which meant a failure in the function's own final commit left that
    change already durably persisted with no way to roll it back — a real
    bug only a live PostgreSQL integration test caught, since a mocked
    Session's commit() never actually flushes anything.
    """
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
    if commit:
        db.commit()
