import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.deps import get_current_user
from app.core.rate_limit import (
    is_locked,
    limiter,
    register_failed_login,
    register_successful_login,
)
from app.core.security import create_access_token, hash_password, verify_password
from app.crud.audit import write_audit_event
from app.crud.user import get_by_username
from app.database import get_db
from app.models.audit_log import AuthEventType
from app.models.session import AuthSession
from app.models.user import User
from app.schemas.auth import ChangePasswordRequest, LoginRequest, LoginResponse, MeResponse, UserOut

router = APIRouter(prefix="/auth", tags=["auth"])

GENERIC_LOGIN_ERROR = "Invalid username or password."


def _client_meta(request: Request) -> tuple[str, str]:
    ip = request.client.host if request.client else "unknown"
    ua = request.headers.get("user-agent", "unknown")
    return ip, ua


@router.post("/login", response_model=LoginResponse)
@limiter.limit("20/minute")
def login(request: Request, payload: LoginRequest, db: Session = Depends(get_db)):
    ip, ua = _client_meta(request)
    user = get_by_username(db, payload.username)

    if user is None:
        write_audit_event(
            db, AuthEventType.login_failure, ip, ua, username_attempted=payload.username, detail="unknown username"
        )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=GENERIC_LOGIN_ERROR)

    if is_locked(user):
        write_audit_event(db, AuthEventType.account_locked, ip, ua, user=user, detail="login attempt while locked")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account temporarily locked due to too many failed attempts. Try again later.",
        )

    if not user.is_active:
        write_audit_event(db, AuthEventType.login_failure, ip, ua, user=user, detail="inactive account")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=GENERIC_LOGIN_ERROR)

    if not verify_password(payload.password, user.password_hash):
        register_failed_login(db, user)
        event = AuthEventType.account_locked if is_locked(user) else AuthEventType.login_failure
        write_audit_event(db, event, ip, ua, user=user, detail="bad password")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=GENERIC_LOGIN_ERROR)

    register_successful_login(db, user)

    jti = uuid.uuid4()
    token, expires_at = create_access_token(user.id, user.role.value, jti)
    session = AuthSession(id=jti, user_id=user.id, expires_at=expires_at, ip_address=ip, user_agent=ua)
    db.add(session)
    db.commit()

    write_audit_event(db, AuthEventType.login_success, ip, ua, user=user)

    return LoginResponse(access_token=token, user=UserOut.model_validate(user))


@router.get("/me", response_model=MeResponse)
def me(user: User = Depends(get_current_user)):
    return MeResponse.model_validate(user)


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(
    request: Request,
    payload: ChangePasswordRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    ip, ua = _client_meta(request)

    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Current password is incorrect.")

    if payload.new_password == payload.current_password:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="New password must differ from the current password.")

    if payload.new_password.lower() == user.username.lower():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="New password must not match your username.")

    user.password_hash = hash_password(payload.new_password)
    user.must_change_password = False
    user.password_changed_at = datetime.now(timezone.utc)
    db.add(user)
    db.commit()

    write_audit_event(db, AuthEventType.password_change, ip, ua, user=user)
    return None


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    ip, ua = _client_meta(request)
    session: AuthSession = user._current_session  # type: ignore[attr-defined]
    session.revoked_at = datetime.now(timezone.utc)
    db.add(session)
    db.commit()

    write_audit_event(db, AuthEventType.logout, ip, ua, user=user)
    return None
