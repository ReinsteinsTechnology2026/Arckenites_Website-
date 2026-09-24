"""Public, unauthenticated Community join flow: submit details -> verify
email via OTP -> set password -> the account becomes a real, active
User(role=community). No step here ever requires an existing session —
same convention as routes_contact.py's public enquiry endpoint.

Account-enumeration note: every response on the /register/* endpoints
below is deliberately generic and identical whether the email is already
registered, already mid-registration, or genuinely new — see
GENERIC_REGISTRATION_MESSAGE in schemas/community.py. The one place this
necessarily breaks down is verify-otp, where success vs. failure has to be
distinguishable to make the flow usable at all; both failure reasons
(wrong code, expired code, too many attempts, no such registration) still
collapse into the same "Invalid or expired verification code." text.
"""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.email import send_email
from app.core.otp import (
    OTP_MAX_ATTEMPTS,
    OTP_MAX_SENDS_PER_WINDOW,
    OTP_SEND_WINDOW_MINUTES,
    generate_otp,
    generate_verification_token,
    hash_otp,
    hash_verification_token,
    mask_email,
    otp_expiry,
    verification_token_expiry,
    verify_otp,
)
from app.core.rate_limit import limiter
from app.core.security import hash_password, validate_password_strength
from app.crud.audit import write_audit_event
from app.crud.system_settings import get_settings
from app.database import get_db
from app.models.audit_log import AuthEventType
from app.models.community import CommunityProfile, CommunityRegistration, CommunityRegistrationStatus
from app.models.user import RoleEnum, User
from app.schemas.community import (
    GENERIC_REGISTRATION_MESSAGE,
    GenericMessageOut,
    ResendCommunityOtpRequest,
    SetCommunityPasswordRequest,
    StartCommunityRegistrationRequest,
    VerifyCommunityOtpRequest,
    VerifyCommunityOtpResponse,
)

router = APIRouter(prefix="/community", tags=["community-auth"])

logger = logging.getLogger("community_auth")

GENERIC_OTP_ERROR = "Invalid or expired verification code."
GENERIC_TOKEN_ERROR = "Your verification session has expired. Please verify your email again."


def _client_meta(request: Request) -> tuple[str, str]:
    ip = request.client.host if request.client else "unknown"
    ua = request.headers.get("user-agent", "unknown")
    return ip, ua


def _username_taken(db: Session, email: str) -> bool:
    return db.scalar(select(User.id).where(func.lower(User.username) == email)) is not None


def _send_otp_email(email: str, full_name: str, otp: str) -> bool:
    first_name = full_name.strip().split(" ")[0] if full_name.strip() else "there"
    subject = "Arckenites Community Email Verification"
    body = (
        f"Hello {first_name},\n\n"
        f"Your Arckenites Community verification code is:\n\n"
        f"{otp}\n\n"
        f"This code expires in 10 minutes.\n\n"
        f"If you did not request this verification, you can ignore this email.\n\n"
        f"Regards,\nArckenites Team"
    )
    # Never logs the OTP itself — only whether the send succeeded.
    return send_email(email, subject, body)


def _within_resend_window(reg: CommunityRegistration, now: datetime) -> bool:
    if reg.otp_window_started_at is None:
        return False
    return (now - reg.otp_window_started_at).total_seconds() < OTP_SEND_WINDOW_MINUTES * 60


def _issue_otp(db: Session, reg: CommunityRegistration, full_name_for_email: str) -> None:
    """Generates and stores a fresh OTP, respecting the send-rate window,
    then emails it. Silently no-ops (rate limited) if the window's send
    cap has already been reached — the caller always returns the same
    generic response either way, so this never becomes an oracle."""
    now = datetime.now(timezone.utc)

    if reg.otp_window_started_at is None or not _within_resend_window(reg, now):
        reg.otp_window_started_at = now
        reg.otp_send_count = 0

    if reg.otp_send_count >= OTP_MAX_SENDS_PER_WINDOW:
        return  # rate limited — caller still returns the generic message

    otp = generate_otp()
    reg.otp_hash = hash_otp(otp)
    reg.otp_expires_at = otp_expiry()
    reg.otp_attempts = 0
    reg.otp_send_count += 1
    reg.status = CommunityRegistrationStatus.pending
    db.add(reg)
    db.commit()

    if not _send_otp_email(reg.email, full_name_for_email, otp):
        logger.warning("Community OTP email failed to send for a pending registration")


@router.post("/register/start", response_model=GenericMessageOut)
@limiter.limit("5/minute")
def start_registration(request: Request, payload: StartCommunityRegistrationRequest, db: Session = Depends(get_db)):
    ip, ua = _client_meta(request)
    email = payload.email  # already lowercased by the schema validator

    # An email already belonging to a real account (any role) never gets an
    # OTP and never reveals that fact.
    if _username_taken(db, email):
        write_audit_event(
            db, AuthEventType.community_registration_started, ip, ua,
            username_attempted=email, detail="email already registered", status="blocked",
        )
        return GenericMessageOut(detail=GENERIC_REGISTRATION_MESSAGE)

    reg = db.scalar(select(CommunityRegistration).where(CommunityRegistration.email == email))
    if reg is None:
        reg = CommunityRegistration(full_name=payload.full_name, mobile_number=payload.mobile_number, email=email)
        db.add(reg)
        db.flush()
    else:
        # Re-submitting details for an in-progress (not yet completed)
        # registration — safe to refresh and restart verification rather
        # than creating a second row for the same email.
        reg.full_name = payload.full_name
        reg.mobile_number = payload.mobile_number

    write_audit_event(db, AuthEventType.community_registration_started, ip, ua, username_attempted=email)
    _issue_otp(db, reg, payload.full_name)
    write_audit_event(db, AuthEventType.community_otp_sent, ip, ua, username_attempted=email)

    return GenericMessageOut(detail=GENERIC_REGISTRATION_MESSAGE)


@router.post("/register/resend-otp", response_model=GenericMessageOut)
@limiter.limit("5/minute")
def resend_otp(request: Request, payload: ResendCommunityOtpRequest, db: Session = Depends(get_db)):
    ip, ua = _client_meta(request)
    email = payload.email

    reg = db.scalar(select(CommunityRegistration).where(CommunityRegistration.email == email))
    if reg is not None and reg.status == CommunityRegistrationStatus.pending:
        _issue_otp(db, reg, reg.full_name)
        write_audit_event(db, AuthEventType.community_otp_resent, ip, ua, username_attempted=email)

    # Identical response whether or not a registration exists, whether or
    # not it was rate-limited — see the module docstring.
    return GenericMessageOut(detail=GENERIC_REGISTRATION_MESSAGE)


@router.post("/register/verify-otp", response_model=VerifyCommunityOtpResponse)
@limiter.limit("10/minute")
def verify_otp_endpoint(request: Request, payload: VerifyCommunityOtpRequest, db: Session = Depends(get_db)):
    ip, ua = _client_meta(request)
    email = payload.email
    invalid = HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=GENERIC_OTP_ERROR)

    reg = db.scalar(select(CommunityRegistration).where(CommunityRegistration.email == email))
    if reg is None or reg.status != CommunityRegistrationStatus.pending or not reg.otp_hash:
        raise invalid

    now = datetime.now(timezone.utc)
    if reg.otp_expires_at is None or reg.otp_expires_at < now:
        raise invalid

    if reg.otp_attempts >= OTP_MAX_ATTEMPTS:
        # Already exhausted — invalidate defensively in case an earlier
        # request didn't get the chance to (e.g. a crash mid-request).
        reg.otp_hash = None
        reg.otp_expires_at = None
        db.add(reg)
        db.commit()
        raise invalid

    if not verify_otp(payload.otp, reg.otp_hash):
        reg.otp_attempts += 1
        if reg.otp_attempts >= OTP_MAX_ATTEMPTS:
            reg.otp_hash = None
            reg.otp_expires_at = None
        db.add(reg)
        db.commit()
        write_audit_event(db, AuthEventType.community_otp_failed, ip, ua, username_attempted=email)
        raise invalid

    # Success — the OTP is single-use, so it's invalidated immediately.
    reg.status = CommunityRegistrationStatus.verified
    reg.verified_at = now
    reg.otp_hash = None
    reg.otp_expires_at = None
    reg.otp_attempts = 0

    token = generate_verification_token()
    reg.verification_token_hash = hash_verification_token(token)
    reg.verification_token_expires_at = verification_token_expiry()
    db.add(reg)
    db.commit()

    write_audit_event(db, AuthEventType.community_otp_verified, ip, ua, username_attempted=email)

    return VerifyCommunityOtpResponse(verification_token=token, expires_in_minutes=15)


@router.post("/register/set-password", response_model=GenericMessageOut)
@limiter.limit("10/minute")
def set_password(request: Request, payload: SetCommunityPasswordRequest, db: Session = Depends(get_db)):
    ip, ua = _client_meta(request)
    token_error = HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=GENERIC_TOKEN_ERROR)

    if payload.new_password != payload.confirm_password:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Passwords do not match.")

    token_hash = hash_verification_token(payload.verification_token)
    reg = db.scalar(select(CommunityRegistration).where(CommunityRegistration.verification_token_hash == token_hash))

    now = datetime.now(timezone.utc)
    if (
        reg is None
        or reg.status != CommunityRegistrationStatus.verified
        or reg.verification_token_expires_at is None
        or reg.verification_token_expires_at < now
    ):
        raise token_error

    if get_settings(db).require_strong_passwords:
        strength_error = validate_password_strength(payload.new_password)
        if strength_error:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=strength_error)

    if _username_taken(db, reg.email):
        # Extremely rare race (e.g. the email was somehow claimed by
        # another flow between OTP verification and this call) — same
        # generic wording, no detail about why.
        raise token_error

    user = User(
        username=reg.email,
        password_hash=hash_password(payload.new_password),
        full_name=reg.full_name,
        role=RoleEnum.community,
        is_active=True,
        must_change_password=False,
        password_changed_at=now,
    )
    db.add(user)
    try:
        db.flush()  # assigns user.id, inside the same transaction as the profile insert below
        profile = CommunityProfile(
            user_id=user.id,
            mobile_number=reg.mobile_number,
            email=reg.email,
            email_verified_at=reg.verified_at or now,
        )
        db.add(profile)
        db.delete(reg)
        db.commit()
    except IntegrityError:
        db.rollback()
        logger.exception("Failed to finalize community account creation")
        raise token_error

    write_audit_event(db, AuthEventType.community_account_created, ip, ua, user=user)

    return GenericMessageOut(detail="Your Community account has been created. You can now log in.")
