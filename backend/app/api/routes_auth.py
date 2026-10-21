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
from app.core.security import create_access_token, hash_password, validate_password_strength, verify_password
from app.core.mfa import _key as require_mfa_key, new_challenge
from app.crud.audit import write_audit_event
from app.crud.permissions import get_effective_permissions
from app.crud.session import revoke_other_sessions
from app.crud.system_settings import get_settings
from app.crud.user import get_by_username
from app.database import get_db
from app.models.audit_log import AuthEventType
from app.models.session import AuthSession
from app.models.mfa import MfaCredential
from app.models.user import RoleEnum, User
from app.schemas.auth import AdminRoleSummary, ChangePasswordRequest, LoginRequest, LoginResponse, MfaChallengeOut, MeResponse, UserOut

router = APIRouter(prefix="/auth", tags=["auth"])

GENERIC_LOGIN_ERROR = "Invalid username or password."


def _client_meta(request: Request) -> tuple[str, str]:
    ip = request.client.host if request.client else "unknown"
    ua = request.headers.get("user-agent", "unknown")
    return ip, ua


def _user_out(db: Session, user: User) -> UserOut:
    """Builds UserOut with permissions/admin_role populated explicitly —
    these need a fresh query (see crud/permissions.py), so bare
    from_attributes on the ORM object isn't enough for those two fields."""
    out = UserOut.model_validate(user)
    out.permissions = get_effective_permissions(db, user)
    if user.admin_role is not None:
        out.admin_role = AdminRoleSummary.model_validate(user.admin_role)
    return out


def finish_login(db: Session, request: Request, user: User, *, mfa_verified: bool) -> LoginResponse:
    ip, ua = _client_meta(request)
    register_successful_login(db, user, commit=False)
    jti = uuid.uuid4()
    token, expires_at = create_access_token(user.id, user.role.value, jti,
                                             ttl_minutes=get_settings(db).session_timeout_minutes)
    db.add(AuthSession(id=jti, user_id=user.id, expires_at=expires_at,
                       ip_address=ip, user_agent=ua, mfa_verified=mfa_verified))
    write_audit_event(db, AuthEventType.login_success, ip, ua, user=user,
                      detail="MFA login" if mfa_verified else None, commit=False)
    db.commit()
    return LoginResponse(access_token=token, user=_user_out(db, user))


def begin_or_finish_login(db: Session, request: Request, user: User, source: str) -> LoginResponse | MfaChallengeOut:
    credential = db.get(MfaCredential, user.id)
    if (credential and credential.enabled_at) or get_settings(db).mfa_required:
        try:
            require_mfa_key()
        except RuntimeError:
            raise HTTPException(status_code=503, detail="MFA service is unavailable.")
        needs_enrollment = credential is None or credential.enabled_at is None
        token = new_challenge(db, user.id, f"enroll_{source}" if needs_enrollment else source)
        db.commit()
        return MfaChallengeOut(challenge=token, enrollment_required=needs_enrollment)
    return finish_login(db, request, user, mfa_verified=False)


@router.post("/login", response_model=LoginResponse | MfaChallengeOut)
@limiter.limit("20/minute")
def login(request: Request, payload: LoginRequest, db: Session = Depends(get_db)):
    ip, ua = _client_meta(request)
    settings_row = get_settings(db)
    user = get_by_username(db, payload.username, payload.portal)

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

    # Maintenance mode blocks everyone except admins — checked after the
    # account/lockout checks above so a disabled/unknown account still gets
    # its normal error rather than a misleading "under maintenance".
    if settings_row.maintenance_mode and user.role != RoleEnum.admin:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The system is currently under maintenance. Please try again later.",
        )

    if not verify_password(payload.password, user.password_hash):
        register_failed_login(db, user)
        event = AuthEventType.account_locked if is_locked(user) else AuthEventType.login_failure
        write_audit_event(db, event, ip, ua, user=user, detail="bad password")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=GENERIC_LOGIN_ERROR)

    # The selected portal is only a requested audience. Authorization comes
    # from the persisted role and is checked before MFA challenges or sessions.
    if payload.portal == "student" and user.role != RoleEnum.student:
        write_audit_event(db, AuthEventType.login_failure, ip, ua, user=user, detail="wrong portal: student")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=GENERIC_LOGIN_ERROR)
    if payload.portal == "staff" and user.role not in (RoleEnum.staff, RoleEnum.admin):
        write_audit_event(db, AuthEventType.login_failure, ip, ua, user=user, detail="wrong portal: staff")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=GENERIC_LOGIN_ERROR)

    return begin_or_finish_login(db, request, user, "password")


@router.get("/me", response_model=MeResponse)
def me(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _user_out(db, user)


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

    if get_settings(db).require_strong_passwords:
        strength_error = validate_password_strength(payload.new_password)
        if strength_error:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=strength_error)

    user.password_hash = hash_password(payload.new_password)
    user.must_change_password = False
    user.password_changed_at = datetime.now(timezone.utc)
    db.add(user)
    try:
        revoke_other_sessions(db, user.id, user._current_session.id)
        write_audit_event(db, AuthEventType.password_change, ip, ua, user=user, commit=False)
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Could not change password. Please try again.")
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
