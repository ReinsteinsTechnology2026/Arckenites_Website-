"""Personal TOTP enrollment, challenge completion and Super Admin policy."""
import base64
import io
from datetime import datetime, timezone

import qrcode
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.api.routes_auth import finish_login
from app.core.deps import get_current_user, require_super_admin
from app.core.mfa import (
    SETUP_TTL, consume_factor, decrypt_secret, encrypt_secret,
    locked_challenge, matching_step, new_secret, provisioning_uri, replace_recovery_codes,
)
from app.core.rate_limit import is_locked, limiter
from app.core.security import verify_password
from app.crud.audit import write_audit_event
from app.crud.session import revoke_all_sessions, revoke_other_sessions
from app.crud.system_settings import get_settings
from app.database import get_db
from app.models.audit_log import AuthEventType
from app.models.mfa import MfaCredential, MfaRecoveryCode
from app.models.session import AuthSession
from app.models.user import RoleEnum, User
from app.schemas.auth import LoginResponse

router = APIRouter(prefix="/auth/mfa", tags=["auth-mfa"])


class ChallengeInput(BaseModel):
    challenge: str = Field(min_length=30, max_length=200)


class VerifyChallengeInput(ChallengeInput):
    code: str = Field(min_length=6, max_length=40)


class PasswordInput(BaseModel):
    password: str = Field(min_length=1, max_length=128)


class ConfirmSetupInput(BaseModel):
    code: str = Field(pattern=r"^[0-9]{6}$")


class ReauthenticateInput(PasswordInput):
    code: str = Field(min_length=6, max_length=40)


class PolicyInput(ReauthenticateInput):
    required: bool


def _meta(request: Request) -> tuple[str, str]:
    return (request.client.host if request.client else "unknown",
            request.headers.get("user-agent", "unknown"))


def _audit(db: Session, request: Request, user: User, detail: str) -> None:
    ip, ua = _meta(request)
    write_audit_event(db, AuthEventType.settings_changed, ip, ua, user=user,
                      detail=detail, module="security", target=str(user.id), status="success", commit=False)


def _credential(db: Session, user_id: int) -> MfaCredential | None:
    return db.scalar(select(MfaCredential).where(MfaCredential.user_id == user_id).with_for_update())


def _enabled(db: Session, user_id: int) -> MfaCredential:
    credential = _credential(db, user_id)
    if credential is None or credential.enabled_at is None:
        raise HTTPException(400, "MFA is not enabled for this account.")
    return credential


def _require_password_and_factor(db: Session, user: User, payload: ReauthenticateInput) -> MfaCredential:
    if not verify_password(payload.password, user.password_hash):
        raise HTTPException(401, "Verification failed.")
    credential = _enabled(db, user.id)
    if not consume_factor(db, credential, payload.code):
        raise HTTPException(401, "Verification failed.")
    return credential


def _qr_data_uri(secret: str, username: str) -> str:
    image = qrcode.make(provisioning_uri(secret, username))
    output = io.BytesIO()
    image.save(output, format="PNG")
    return "data:image/png;base64," + base64.b64encode(output.getvalue()).decode("ascii")


def _begin_setup(db: Session, user: User) -> dict:
    credential = _credential(db, user.id)
    if credential is not None and credential.enabled_at is not None:
        raise HTTPException(409, "MFA is already enabled.")
    secret = new_secret()
    if credential is None:
        credential = MfaCredential(user_id=user.id)
        db.add(credential)
    credential.pending_ciphertext = encrypt_secret(secret)
    credential.pending_expires_at = datetime.now(timezone.utc) + SETUP_TTL
    credential.pending_attempts = 0
    db.commit()
    return {"qr_code": _qr_data_uri(secret, user.username),
            "expires_in_seconds": int(SETUP_TTL.total_seconds())}


def _confirm_setup(db: Session, user: User, code: str) -> list[str]:
    credential = _credential(db, user.id)
    now = datetime.now(timezone.utc)
    expiry = (credential.pending_expires_at if credential else None)
    if expiry is not None and expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    if (credential is None or credential.enabled_at is not None or not credential.pending_ciphertext
            or expiry is None or expiry < now or credential.pending_attempts >= 5):
        raise HTTPException(400, "MFA setup expired. Start again.")
    step = matching_step(decrypt_secret(credential.pending_ciphertext), code)
    if step is None:
        credential.pending_attempts += 1
        db.commit()
        raise HTTPException(400, "Invalid authenticator code.")
    credential.secret_ciphertext = credential.pending_ciphertext
    credential.pending_ciphertext = None
    credential.pending_expires_at = None
    credential.pending_attempts = 0
    credential.enabled_at = now
    credential.last_used_step = step
    db.add(credential)
    return replace_recovery_codes(db, user.id)


def _challenge_user(db: Session, token: str, *, enrollment: bool):
    challenge = locked_challenge(db, token)
    if challenge is None or challenge.source.startswith("enroll_") != enrollment:
        raise HTTPException(401, "MFA challenge expired or invalid. Sign in again.")
    user = db.get(User, challenge.user_id)
    if user is None or not user.is_active or is_locked(user):
        raise HTTPException(401, "MFA challenge expired or invalid. Sign in again.")
    if get_settings(db).maintenance_mode and user.role != RoleEnum.admin:
        raise HTTPException(503, "The system is currently under maintenance.")
    return challenge, user


@router.get("/status")
def status(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    credential = db.get(MfaCredential, user.id)
    return {"enabled": bool(credential and credential.enabled_at),
            "required_by_policy": get_settings(db).mfa_required,
            "recovery_codes_remaining": db.scalar(select(func.count()).select_from(MfaRecoveryCode).where(
                MfaRecoveryCode.user_id == user.id))}


@router.post("/setup/start")
@limiter.limit("5/minute")
def setup_start(request: Request, payload: PasswordInput, response: Response,
                db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if not verify_password(payload.password, user.password_hash):
        raise HTTPException(401, "Verification failed.")
    response.headers["Cache-Control"] = "no-store"
    return _begin_setup(db, user)


@router.post("/setup/confirm")
@limiter.limit("10/minute")
def setup_confirm(request: Request, payload: ConfirmSetupInput, response: Response,
                  db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    codes = _confirm_setup(db, user, payload.code)
    current = user._current_session
    current.mfa_verified = True
    revoke_other_sessions(db, user.id, current.id)
    _audit(db, request, user, "Personal MFA enabled")
    db.commit()
    response.headers["Cache-Control"] = "no-store"
    return {"enabled": True, "recovery_codes": codes}


@router.post("/enrollment/start")
@limiter.limit("5/minute")
def enrollment_start(request: Request, payload: ChallengeInput, response: Response,
                     db: Session = Depends(get_db)):
    challenge, user = _challenge_user(db, payload.challenge, enrollment=True)
    if not get_settings(db).mfa_required:
        raise HTTPException(401, "Enrollment challenge is no longer valid.")
    response.headers["Cache-Control"] = "no-store"
    return _begin_setup(db, user)


@router.post("/enrollment/confirm")
@limiter.limit("10/minute")
def enrollment_confirm(request: Request, payload: VerifyChallengeInput, response: Response,
                       db: Session = Depends(get_db)):
    challenge, user = _challenge_user(db, payload.challenge, enrollment=True)
    try:
        codes = _confirm_setup(db, user, payload.code)
    except HTTPException:
        challenge.attempts += 1
        db.commit()
        raise
    challenge.used_at = datetime.now(timezone.utc)
    _audit(db, request, user, "Personal MFA enabled during sign-in")
    login = finish_login(db, request, user, mfa_verified=True)
    response.headers["Cache-Control"] = "no-store"
    return {**login.model_dump(), "recovery_codes": codes}


@router.post("/challenge/verify", response_model=LoginResponse)
@limiter.limit("10/minute")
def verify_challenge(request: Request, payload: VerifyChallengeInput, response: Response,
                     db: Session = Depends(get_db)):
    challenge, user = _challenge_user(db, payload.challenge, enrollment=False)
    credential = _enabled(db, user.id)
    if not consume_factor(db, credential, payload.code):
        challenge.attempts += 1
        db.commit()
        raise HTTPException(401, "Invalid authenticator or recovery code.")
    challenge.used_at = datetime.now(timezone.utc)
    db.add(challenge)
    response.headers["Cache-Control"] = "no-store"
    return finish_login(db, request, user, mfa_verified=True)


@router.post("/disable", status_code=204)
@limiter.limit("5/minute")
def disable(request: Request, payload: ReauthenticateInput, db: Session = Depends(get_db),
            user: User = Depends(get_current_user)):
    if get_settings(db).mfa_required:
        raise HTTPException(403, "MFA is required by organization policy and cannot be disabled.")
    credential = _require_password_and_factor(db, user, payload)
    credential.secret_ciphertext = None
    credential.pending_ciphertext = None
    credential.pending_expires_at = None
    credential.enabled_at = None
    credential.last_used_step = None
    db.execute(delete(MfaRecoveryCode).where(MfaRecoveryCode.user_id == user.id))
    revoke_all_sessions(db, user.id)
    _audit(db, request, user, "Personal MFA disabled")
    db.commit()


@router.post("/recovery/regenerate")
@limiter.limit("5/minute")
def regenerate(request: Request, payload: ReauthenticateInput, response: Response,
               db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    _require_password_and_factor(db, user, payload)
    codes = replace_recovery_codes(db, user.id)
    revoke_other_sessions(db, user.id, user._current_session.id)
    _audit(db, request, user, "MFA recovery codes regenerated")
    db.commit()
    response.headers["Cache-Control"] = "no-store"
    return {"recovery_codes": codes}


@router.post("/policy")
@limiter.limit("5/minute")
def update_policy(request: Request, payload: PolicyInput, db: Session = Depends(get_db),
                  actor: User = Depends(require_super_admin)):
    _require_password_and_factor(db, actor, payload)
    row = get_settings(db)
    row.mfa_required = payload.required
    row.updated_by_id = actor.id
    if payload.required:
        db.execute(update(AuthSession).where(AuthSession.mfa_verified.is_(False),
                                             AuthSession.revoked_at.is_(None)).values(
            revoked_at=datetime.now(timezone.utc)))
    _audit(db, request, actor, "Organization MFA policy enabled" if payload.required else
           "Organization MFA policy disabled")
    db.commit()
    return {"required": row.mfa_required}
