import uuid
from datetime import datetime, timezone

import jwt
from fastapi import Depends, HTTPException, WebSocket, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.config import settings
from app.core.security import decode_access_token
from app.database import get_db
from app.models.session import AuthSession
from app.models.user import User

bearer_scheme = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
        headers={"WWW-Authenticate": "Bearer"},
    )

    if credentials is None:
        raise unauthorized

    try:
        payload = decode_access_token(credentials.credentials)
    except jwt.PyJWTError:
        raise unauthorized

    try:
        jti = uuid.UUID(payload["jti"])
        user_id = int(payload["sub"])
    except (KeyError, ValueError):
        raise unauthorized

    # Real server-side revocation check — a logged-out or revoked token is
    # rejected here even though its JWT signature is still cryptographically
    # valid. This is what makes logout actually work, not just look like it.
    session = db.get(AuthSession, jti)
    if session is None or session.revoked_at is not None:
        raise unauthorized
    if session.expires_at < datetime.now(timezone.utc):
        raise unauthorized

    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise unauthorized

    # Stash the session for the logout endpoint to revoke without a second lookup.
    user._current_session = session  # type: ignore[attr-defined]
    return user


def require_role(*allowed_roles: str):
    def _dependency(user: User = Depends(get_current_user)) -> User:
        if user.role.value not in allowed_roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")
        return user

    return _dependency


async def get_ws_user(websocket: WebSocket, db: Session, *, required_role: str | None = None) -> User | None:
    """WebSocket equivalent of get_current_user. Browsers can't set custom
    headers on the WS handshake, so the token travels as ?token=<jwt>
    instead of an Authorization header. CORSMiddleware doesn't cover
    WebSocket routes at all, so the Origin check here is what stands in
    for it on this path specifically. Closes the socket (code 1008)
    instead of raising on any failure, since there's no HTTP response to
    return an error body on."""
    origin = websocket.headers.get("origin")
    if origin is not None and origin not in settings.cors_origin_list:
        await websocket.close(code=1008)
        return None

    token = websocket.query_params.get("token")
    if not token:
        await websocket.close(code=1008)
        return None

    try:
        payload = decode_access_token(token)
    except jwt.PyJWTError:
        await websocket.close(code=1008)
        return None

    try:
        jti = uuid.UUID(payload["jti"])
        user_id = int(payload["sub"])
    except (KeyError, ValueError):
        await websocket.close(code=1008)
        return None

    session = db.get(AuthSession, jti)
    if session is None or session.revoked_at is not None:
        await websocket.close(code=1008)
        return None
    if session.expires_at < datetime.now(timezone.utc):
        await websocket.close(code=1008)
        return None

    user = db.get(User, user_id)
    if user is None or not user.is_active:
        await websocket.close(code=1008)
        return None

    if required_role is not None and user.role.value != required_role:
        await websocket.close(code=1008)
        return None

    return user
