from datetime import datetime, timedelta, timezone

from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy.orm import Session

from app.models.user import User

# IP-keyed limiter guards the /login endpoint itself against brute force
# spraying from a single source. In-memory (per-process) — fine at current
# scale; revisit with a shared backend (e.g. Redis) if the API ever runs
# multiple worker processes.
limiter = Limiter(key_func=get_remote_address)

MAX_FAILED_ATTEMPTS = 5
LOCKOUT_MINUTES = 15


def is_locked(user: User) -> bool:
    return user.locked_until is not None and user.locked_until > datetime.now(timezone.utc)


def register_failed_login(db: Session, user: User) -> None:
    """DB-backed per-account lockout: catches brute force spread across many
    IPs (which the IP-keyed limiter alone would miss)."""
    user.failed_login_count += 1
    if user.failed_login_count >= MAX_FAILED_ATTEMPTS:
        user.locked_until = datetime.now(timezone.utc) + timedelta(minutes=LOCKOUT_MINUTES)
    db.add(user)
    db.commit()


def register_successful_login(db: Session, user: User) -> None:
    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = datetime.now(timezone.utc)
    db.add(user)
    db.commit()
