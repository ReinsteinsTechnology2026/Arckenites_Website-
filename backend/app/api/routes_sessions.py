import uuid

from fastapi import APIRouter, Body, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.deps import get_current_user
from app.core.security import verify_password
from app.core.rate_limit import limiter
from app.crud.audit import write_audit_event
from app.crud.session import list_active_sessions, revoke_session
from app.database import get_db
from app.models.audit_log import AuthEventType
from app.models.user import RoleEnum, User
from app.schemas.sessions import SessionOut

router = APIRouter(prefix="/me/sessions", tags=["sessions"])


class SessionReauth(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)


def _verify_student_reauth(user: User, payload: SessionReauth | None) -> None:
    # Existing admin session controls retain their established contract.
    if user.role == RoleEnum.student and (payload is None or not verify_password(payload.current_password, user.password_hash)):
        raise HTTPException(status_code=403, detail="Enter your current password to end another session.")


def _device_label(user_agent: str | None) -> str:
    """Small, dependency-free UA sniff — good enough for a friendly label,
    not meant to be a precise device-detection library."""
    if not user_agent:
        return "Unknown device"
    ua = user_agent.lower()

    if "edg/" in ua:
        browser = "Edge"
    elif "chrome/" in ua and "chromium" not in ua:
        browser = "Chrome"
    elif "firefox/" in ua:
        browser = "Firefox"
    elif "safari/" in ua and "chrome/" not in ua:
        browser = "Safari"
    else:
        browser = "Browser"

    if "windows" in ua:
        os_name = "Windows"
    elif "mac os" in ua or "macintosh" in ua:
        os_name = "macOS"
    elif "android" in ua:
        os_name = "Android"
    elif "iphone" in ua or "ipad" in ua:
        os_name = "iOS"
    elif "linux" in ua:
        os_name = "Linux"
    else:
        os_name = "Unknown OS"

    return f"{browser} / {os_name}"


@router.get("", response_model=list[SessionOut])
def list_my_sessions(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    current_session_id = getattr(user, "_current_session", None)
    current_id = current_session_id.id if current_session_id else None

    sessions = list_active_sessions(db, user.id)
    return [
        SessionOut(
            id=s.id, device_label=_device_label(s.user_agent), ip_address=s.ip_address,
            issued_at=s.issued_at, is_current=(s.id == current_id),
        )
        for s in sessions
    ]


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("5/minute")
def revoke_my_session(
    session_id: uuid.UUID,
    request: Request,
    payload: SessionReauth | None = Body(default=None),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _verify_student_reauth(user, payload)
    current_session_id = getattr(user, "_current_session", None)
    if current_session_id and session_id == current_session_id.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You can't revoke your current session from here — use Logout instead.",
        )

    revoked = revoke_session(db, user.id, session_id)
    if not revoked:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found.")
    db.commit()

    ip = request.client.host if request.client else "unknown"
    ua = request.headers.get("user-agent", "unknown")
    write_audit_event(
        db, AuthEventType.session_revoked, ip, ua, user=user, detail="Revoked a session from Active Sessions",
        module="sessions", status="success",
    )
    return None


@router.post("/revoke-others", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("5/minute")
def revoke_other_sessions(
    request: Request,
    payload: SessionReauth | None = Body(default=None),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _verify_student_reauth(user, payload)
    current_session_id = getattr(user, "_current_session", None)
    current_id = current_session_id.id if current_session_id else None

    for s in list_active_sessions(db, user.id):
        if s.id != current_id:
            revoke_session(db, user.id, s.id)
    db.commit()

    ip = request.client.host if request.client else "unknown"
    ua = request.headers.get("user-agent", "unknown")
    write_audit_event(
        db, AuthEventType.session_revoked, ip, ua, user=user, detail="Revoked all other sessions",
        module="sessions", status="success",
    )
    return None
