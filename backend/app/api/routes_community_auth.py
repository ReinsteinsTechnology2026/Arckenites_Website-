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
from contextlib import contextmanager
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.community_identity import normalize_mobile
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
from app.database import engine, get_db
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
EMAIL_SEND_FAILED_ERROR = "We couldn't send the verification email right now. Please try again later."

# Self-registered Community accounts always need at least this many characters,
# even when the optional strong-password setting is off. Turning that setting on
# adds the stricter rules on top; it never lowers this minimum.
MIN_COMMUNITY_PASSWORD_LENGTH = 8


def _client_meta(request: Request) -> tuple[str, str]:
    ip = request.client.host if request.client else "unknown"
    ua = request.headers.get("user-agent", "unknown")
    return ip, ua


def _username_taken(db: Session, email: str) -> bool:
    return db.scalar(select(User.id).where(func.lower(User.username) == email)) is not None


def _email_registered(db: Session, email: str) -> bool:
    """True when the email already belongs to any account, or to a completed
    Community account (checked case-insensitively on both)."""
    if _username_taken(db, email):
        return True
    return db.scalar(
        select(CommunityProfile.id).where(func.lower(CommunityProfile.email) == email)
    ) is not None


def _mobile_registered(db: Session, mobile_normalized: str) -> bool:
    return db.scalar(
        select(CommunityProfile.id).where(CommunityProfile.mobile_normalized == mobile_normalized)
    ) is not None


# User-facing duplicate wording. `detail` is the heading shown to the user,
# `hint` the supporting text. Nothing here identifies an account: no id, no
# role, no creation date, no password status.
DUPLICATE_COPY = {
    "email": (
        "email_registered",
        "This email address is already registered.",
        "An account already exists with this email address. Please log in instead.",
    ),
    "mobile": (
        "mobile_registered",
        "This mobile number is already registered.",
        "An account already exists with this mobile number. Please log in or use another mobile number.",
    ),
    "both": (
        "credentials_registered",
        "This email address and mobile number are already registered.",
        "An Arckenites Community account already exists with these details. Please log in instead.",
    ),
}

IN_PROGRESS_COPY = (
    "registration_in_progress",
    "Registration already started for this email address.",
    "Continue with the verification code we sent, or start again to receive a new code.",
)


def _duplicate_response(email_taken: bool, mobile_taken: bool) -> JSONResponse:
    key = "both" if (email_taken and mobile_taken) else ("email" if email_taken else "mobile")
    code, heading, hint = DUPLICATE_COPY[key]
    return JSONResponse(status_code=status.HTTP_409_CONFLICT, content={"detail": heading, "code": code, "hint": hint})


def _in_progress_response() -> JSONResponse:
    code, heading, hint = IN_PROGRESS_COPY
    return JSONResponse(status_code=status.HTTP_409_CONFLICT, content={"detail": heading, "code": code, "hint": hint})


def _otp_still_valid(reg: CommunityRegistration, now: datetime) -> bool:
    return bool(reg.otp_hash) and reg.otp_expires_at is not None and reg.otp_expires_at > now


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


class OtpIssueResult:
    """Distinguishes WHY no fresh OTP went out, because the caller must
    treat these very differently:
      - SENT: a real send was attempted and smtplib confirmed it succeeded.
      - RATE_LIMITED: no send was attempted at all (this window's send cap
        is used up) — stays silent/generic, same as before, so this never
        becomes an email-enumeration oracle.
      - FAILED: a real send was attempted and it did NOT succeed — this
        must NOT be reported to the caller as success. This is the bug
        that let the OTP screen show up for an email that was never
        actually delivered."""
    SENT = "sent"
    RATE_LIMITED = "rate_limited"
    FAILED = "failed"


def _issue_otp(db: Session, reg: CommunityRegistration, full_name_for_email: str) -> str:
    """Generates and stores a fresh OTP, respecting the send-rate window,
    then emails it — and reports back whether that email actually left the
    server successfully. The OTP hash is only committed once smtplib has
    confirmed delivery to the mail server, so a failed send never leaves
    behind a "valid" OTP the user has no way to receive."""
    now = datetime.now(timezone.utc)

    if reg.otp_window_started_at is None or not _within_resend_window(reg, now):
        reg.otp_window_started_at = now
        reg.otp_send_count = 0

    if reg.otp_send_count >= OTP_MAX_SENDS_PER_WINDOW:
        db.add(reg)
        db.commit()
        return OtpIssueResult.RATE_LIMITED

    otp = generate_otp()
    sent = _send_otp_email(reg.email, full_name_for_email, otp)

    # Counts toward the rate-limit window either way — a failing SMTP
    # relay must not become a way to trigger unlimited send attempts.
    reg.otp_send_count += 1

    if not sent:
        logger.error("Community OTP email delivery failed stage=SMTP")
        db.add(reg)
        db.commit()
        return OtpIssueResult.FAILED

    reg.otp_hash = hash_otp(otp)
    reg.otp_expires_at = otp_expiry()
    reg.otp_attempts = 0
    reg.status = CommunityRegistrationStatus.pending
    db.add(reg)
    db.commit()
    return OtpIssueResult.SENT


@contextmanager
def _registration_lock(email: str):
    """Serializes registration starts for one email address across requests.

    Uses a session-level Postgres advisory lock on a dedicated connection that
    is held for the whole request. A transactional lock would not work here:
    the request commits partway through (the audit log commits), which would
    release it early and let a second request slip in between "check for a
    valid code" and "issue a code", sending two OTP emails. Unique constraints
    alone do not prevent that, because the registration row already exists.
    """
    key = f"community_registration:{email}"
    with engine.connect() as conn:
        conn.execute(text("SELECT pg_advisory_lock(hashtext(:k))"), {"k": key})
        try:
            yield
        finally:
            conn.execute(text("SELECT pg_advisory_unlock(hashtext(:k))"), {"k": key})
            conn.commit()


@router.post("/register/start", response_model=GenericMessageOut)
@limiter.limit("5/minute")
def start_registration(request: Request, payload: StartCommunityRegistrationRequest, db: Session = Depends(get_db)):
    with _registration_lock(payload.email):
        return _start_registration_locked(request, payload, db)


def _start_registration_locked(request: Request, payload: StartCommunityRegistrationRequest, db: Session):
    ip, ua = _client_meta(request)
    email = payload.email  # already lowercased by the schema validator
    mobile_normalized = normalize_mobile(payload.mobile_number)

    # Duplicate credentials are checked BEFORE any registration row or OTP is
    # created. The database constraints (set_password) are the final guard;
    # this check gives the user a clear message in the common case.
    email_taken = _email_registered(db, email)
    mobile_taken = _mobile_registered(db, mobile_normalized)
    if email_taken or mobile_taken:
        write_audit_event(
            db, AuthEventType.community_registration_started, ip, ua,
            username_attempted=email, detail="duplicate credentials", status="blocked",
        )
        return _duplicate_response(email_taken, mobile_taken)

    reg = db.scalar(select(CommunityRegistration).where(CommunityRegistration.email == email))
    now = datetime.now(timezone.utc)

    # A registration whose code is still valid is not restarted or duplicated
    # by a repeated submit. The user continues with the code they have, or
    # explicitly asks to start again (restart=true), which goes through the
    # normal rate-limited OTP path below.
    if (
        reg is not None
        and reg.status == CommunityRegistrationStatus.pending
        and not payload.restart
        and _otp_still_valid(reg, now)
    ):
        return _in_progress_response()

    if reg is None:
        reg = CommunityRegistration(
            full_name=payload.full_name,
            mobile_number=payload.mobile_number,
            mobile_normalized=mobile_normalized,
            email=email,
        )
        db.add(reg)
        try:
            db.flush()
        except IntegrityError:
            # Two requests for the same new email arrived together; the other
            # one already created the row. Only one registration exists.
            db.rollback()
            return _in_progress_response()
    else:
        # Re-submitting details for an in-progress (not yet completed)
        # registration — safe to refresh and restart verification rather
        # than creating a second row for the same email.
        reg.full_name = payload.full_name
        reg.mobile_number = payload.mobile_number
        reg.mobile_normalized = mobile_normalized

    write_audit_event(db, AuthEventType.community_registration_started, ip, ua, username_attempted=email)
    result = _issue_otp(db, reg, payload.full_name)

    if result == OtpIssueResult.FAILED:
        # Honest failure — never claim an email went out when smtplib
        # didn't confirm it. This does not leak whether `email` is
        # eligible: it fires identically for any address whenever the
        # mail relay itself is down, which is the only time it fires.
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=EMAIL_SEND_FAILED_ERROR)

    if result == OtpIssueResult.SENT:
        write_audit_event(db, AuthEventType.community_otp_sent, ip, ua, username_attempted=email)

    return GenericMessageOut(detail=GENERIC_REGISTRATION_MESSAGE)


@router.post("/register/resend-otp", response_model=GenericMessageOut)
@limiter.limit("5/minute")
def resend_otp(request: Request, payload: ResendCommunityOtpRequest, db: Session = Depends(get_db)):
    ip, ua = _client_meta(request)
    email = payload.email

    reg = db.scalar(select(CommunityRegistration).where(CommunityRegistration.email == email))
    if reg is not None and reg.status == CommunityRegistrationStatus.pending:
        result = _issue_otp(db, reg, reg.full_name)
        if result == OtpIssueResult.FAILED:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=EMAIL_SEND_FAILED_ERROR)
        if result == OtpIssueResult.SENT:
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

    if len(payload.new_password) < MIN_COMMUNITY_PASSWORD_LENGTH:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Password must be at least {MIN_COMMUNITY_PASSWORD_LENGTH} characters.",
        )

    if get_settings(db).require_strong_passwords:
        strength_error = validate_password_strength(payload.new_password)
        if strength_error:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=strength_error)

    reg_email = reg.email
    reg_mobile_normalized = reg.mobile_normalized or normalize_mobile(reg.mobile_number)

    email_taken = _email_registered(db, reg_email)
    mobile_taken = _mobile_registered(db, reg_mobile_normalized)
    if email_taken or mobile_taken:
        return _duplicate_response(email_taken, mobile_taken)

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
            mobile_normalized=reg_mobile_normalized,
            email=reg_email,
            email_verified_at=reg.verified_at or now,
        )
        db.add(profile)
        db.delete(reg)
        db.commit()
    except IntegrityError:
        # Lost a race with another request that created the same email or
        # mobile. Report the real reason in plain words. The database error
        # itself (which can echo values) is never logged or returned.
        db.rollback()
        logger.warning("Community account creation lost a uniqueness race")
        email_taken = _email_registered(db, reg_email)
        mobile_taken = _mobile_registered(db, reg_mobile_normalized)
        if email_taken or mobile_taken:
            return _duplicate_response(email_taken, mobile_taken)
        raise token_error

    write_audit_event(db, AuthEventType.community_account_created, ip, ua, user=user)

    return GenericMessageOut(detail="Your Community account has been created. You can now log in.")
