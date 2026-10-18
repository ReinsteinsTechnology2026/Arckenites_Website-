from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models.session import AuthSession


def revoke_all_sessions(db: Session, user_id: int) -> None:
    """Force-logout-everywhere for a given user. Caller commits."""
    db.execute(
        update(AuthSession)
        .where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=datetime.now(timezone.utc))
    )


def revoke_other_sessions(db: Session, user_id: int, current_session_id) -> None:
    """End other active sessions after a self-service password change.

    The current session stays usable so the existing onboarding flow can
    continue to profile completion without another login.
    """
    db.execute(
        update(AuthSession)
        .where(AuthSession.user_id == user_id,
               AuthSession.id != current_session_id,
               AuthSession.revoked_at.is_(None))
        .values(revoked_at=datetime.now(timezone.utc))
    )


def list_active_sessions(db: Session, user_id: int) -> list[AuthSession]:
    now = datetime.now(timezone.utc)
    return list(
        db.scalars(
            select(AuthSession)
            .where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None), AuthSession.expires_at > now)
            .order_by(AuthSession.issued_at.desc())
        ).all()
    )


def revoke_session(db: Session, user_id: int, session_id) -> bool:
    """Revoke one session belonging to user_id. Returns False if no such
    active session exists for that user (caller decides how to respond —
    self-service callers must only ever revoke their own sessions, so this
    scoping by user_id is the actual security boundary, not just a filter)."""
    session = db.get(AuthSession, session_id)
    if session is None or session.user_id != user_id or session.revoked_at is not None:
        return False
    session.revoked_at = datetime.now(timezone.utc)
    db.add(session)
    return True
