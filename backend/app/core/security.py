import re
import uuid
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from app.config import settings

JWT_ALGORITHM = "HS256"


def validate_password_strength(password: str) -> str | None:
    """Returns an error message if the password fails the strong-password
    policy, or None if it passes. Only called when Settings > Security >
    "Require strong passwords" is enabled — off by default, so existing
    accounts aren't retroactively broken."""
    if len(password) < 8:
        return "Password must be at least 8 characters."
    if not re.search(r"[A-Z]", password):
        return "Password must include at least one uppercase letter."
    if not re.search(r"[a-z]", password):
        return "Password must include at least one lowercase letter."
    if not re.search(r"\d", password):
        return "Password must include at least one number."
    if not re.search(r"[^A-Za-z0-9]", password):
        return "Password must include at least one symbol."
    return None


def hash_password(plain_password: str) -> str:
    return bcrypt.hashpw(plain_password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain_password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(plain_password.encode("utf-8"), password_hash.encode("utf-8"))


def create_access_token(user_id: int, role: str, jti: uuid.UUID, ttl_minutes: int | None = None) -> tuple[str, datetime]:
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(minutes=ttl_minutes if ttl_minutes is not None else settings.jwt_ttl_min)
    payload = {
        "sub": str(user_id),
        "role": role,
        "jti": str(jti),
        "iat": now,
        "exp": expires_at,
    }
    token = jwt.encode(payload, settings.jwt_secret, algorithm=JWT_ALGORITHM)
    return token, expires_at


def decode_access_token(token: str) -> dict:
    """Raises jwt.PyJWTError on any invalid/expired token — callers must catch it."""
    return jwt.decode(token, settings.jwt_secret, algorithms=[JWT_ALGORITHM])
