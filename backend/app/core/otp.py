"""OTP generation/verification for Community email verification.

Deliberately reuses the app's existing password hashing (bcrypt, via
core/security.py) for OTP storage rather than inventing a second hashing
scheme — the OTP is a short-lived secret that must only ever be checked,
never recovered, exactly like a password. `secrets`, not `random`, is the
only source of randomness used here.
"""

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from app.core.security import hash_password, verify_password

OTP_LENGTH = 6
OTP_TTL_MINUTES = 10
OTP_MAX_ATTEMPTS = 5

# Resend rate limit: at most this many OTP sends per rolling window.
OTP_MAX_SENDS_PER_WINDOW = 3
OTP_SEND_WINDOW_MINUTES = 15


def generate_otp() -> str:
    """A cryptographically random 6-digit code, zero-padded — secrets, not
    random, and never containing anything derived from the email/name so
    it can't be guessed from other known data."""
    return f"{secrets.randbelow(10 ** OTP_LENGTH):0{OTP_LENGTH}d}"


def hash_otp(otp: str) -> str:
    return hash_password(otp)


def verify_otp(otp: str, otp_hash: str) -> bool:
    return verify_password(otp, otp_hash)


def otp_expiry() -> datetime:
    return datetime.now(timezone.utc) + timedelta(minutes=OTP_TTL_MINUTES)


def generate_verification_token() -> str:
    """A one-time, single-purpose credential proving OTP verification
    succeeded — handed to the frontend once, only ever used to call
    set-password. Not a JWT (doesn't need role/permission claims, doesn't
    need to be independently decodable)."""
    return secrets.token_urlsafe(32)


def hash_verification_token(token: str) -> str:
    """Plain SHA-256, deliberately not bcrypt — same reasoning as the VM
    Lab Agent's bearer token (core/deps.py hash_vm_agent_token): this is a
    32-byte cryptographically random value, not a human-guessable secret,
    so a fast, indexable digest is what lets set-password look the
    registration up by an equality match in one query rather than
    iterating every pending registration to bcrypt-compare each one."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def verification_token_expiry() -> datetime:
    return datetime.now(timezone.utc) + timedelta(minutes=15)


def mask_email(email: str) -> str:
    """su****@gmail.com — shown to the user as confirmation of where the
    OTP was sent, without echoing the full address back (matches the
    frontend mockup exactly)."""
    local, _, domain = email.partition("@")
    if not domain:
        return email
    visible = local[:2]
    return f"{visible}{'*' * max(len(local) - len(visible), 4)}@{domain}"
