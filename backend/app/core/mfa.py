"""TOTP and single-use recovery primitives; no portal access is issued here."""
import base64
import hashlib
import hmac
import re
import secrets
import struct
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models.mfa import MfaChallenge, MfaCredential, MfaRecoveryCode

CHALLENGE_TTL = timedelta(minutes=5)
SETUP_TTL = timedelta(minutes=10)
MAX_CHALLENGE_ATTEMPTS = 5
RECOVERY_COUNT = 8


class MfaUnavailable(RuntimeError):
    """MFA cannot run because of server configuration (key missing/invalid/wrong)."""


def _key() -> bytes:
    try:
        value = settings.mfa_encryption_key.strip().encode("ascii")
        raw = base64.urlsafe_b64decode(value)
        if len(raw) != 32:
            raise ValueError("wrong length")
        Fernet(value)
    except (ValueError, UnicodeError, base64.binascii.Error):
        raise MfaUnavailable("MFA encryption key is unavailable or invalid")
    return value


def encrypt_secret(secret: str) -> str:
    return Fernet(_key()).encrypt(secret.encode("ascii")).decode("ascii")


def decrypt_secret(ciphertext: str) -> str:
    try:
        return Fernet(_key()).decrypt(ciphertext.encode("ascii")).decode("ascii")
    except InvalidToken as exc:
        raise MfaUnavailable("MFA secret cannot be decrypted") from exc


def new_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def provisioning_uri(secret: str, username: str) -> str:
    issuer = "Arckenites"
    label = quote(f"{issuer}:{username}", safe="")
    return f"otpauth://totp/{label}?secret={secret}&issuer={quote(issuer)}&algorithm=SHA1&digits=6&period=30"


def totp_for_step(secret: str, step: int) -> str:
    key = base64.b32decode(secret + "=" * ((-len(secret)) % 8), casefold=False)
    digest = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 15
    value = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7fffffff
    return f"{value % 1000000:06d}"


def matching_step(secret: str, code: str, *, now: float | None = None, after_step: int | None = None) -> int | None:
    if len(code) != 6 or not code.isascii() or not code.isdecimal():
        return None
    current = int((time.time() if now is None else now) // 30)
    for step in (current - 1, current, current + 1):
        if step >= 0 and (after_step is None or step > after_step) and hmac.compare_digest(totp_for_step(secret, step), code):
            return step
    return None


def recovery_hash(code: str) -> str:
    key = base64.urlsafe_b64decode(_key())
    normalized = code.strip().replace("-", "").upper()
    if not re.fullmatch(r"[0-9A-F]{32}", normalized):
        raise ValueError("Invalid recovery code format")
    return hmac.new(key, normalized.encode("ascii"), hashlib.sha256).hexdigest()


def replace_recovery_codes(db: Session, user_id: int) -> list[str]:
    db.execute(delete(MfaRecoveryCode).where(MfaRecoveryCode.user_id == user_id))
    codes = [secrets.token_hex(16).upper() for _ in range(RECOVERY_COUNT)]
    db.add_all(MfaRecoveryCode(user_id=user_id, code_hash=recovery_hash(code)) for code in codes)
    return ["-".join(code[i:i + 8] for i in range(0, 32, 8)) for code in codes]


def consume_factor(db: Session, credential: MfaCredential, code: str, *, allow_recovery: bool = True) -> bool:
    if not credential.secret_ciphertext or not credential.enabled_at:
        return False
    step = matching_step(decrypt_secret(credential.secret_ciphertext), code,
                         after_step=credential.last_used_step)
    if step is not None:
        credential.last_used_step = step
        db.add(credential)
        return True
    if allow_recovery and re.fullmatch(r"[0-9A-Fa-f]{32}", code.replace("-", "")):
        digest = recovery_hash(code)
        row = db.scalar(select(MfaRecoveryCode).where(
            MfaRecoveryCode.user_id == credential.user_id,
            MfaRecoveryCode.code_hash == digest).with_for_update())
        if row:
            db.delete(row)
            return True
    return False


def new_challenge(db: Session, user_id: int, source: str) -> str:
    token = secrets.token_urlsafe(32)
    db.add(MfaChallenge(user_id=user_id, token_hash=hashlib.sha256(token.encode()).hexdigest(),
                        expires_at=datetime.now(timezone.utc) + CHALLENGE_TTL, source=source))
    return token


def locked_challenge(db: Session, token: str, *, source: str | None = None) -> MfaChallenge | None:
    if len(token) > 200 or len(token) < 30:
        return None
    row = db.scalar(select(MfaChallenge).where(
        MfaChallenge.token_hash == hashlib.sha256(token.encode()).hexdigest()).with_for_update())
    now = datetime.now(timezone.utc)
    expiry = row.expires_at.replace(tzinfo=timezone.utc) if row and row.expires_at.tzinfo is None else (row.expires_at if row else None)
    if (row is None or row.used_at is not None or expiry < now
            or row.attempts >= MAX_CHALLENGE_ATTEMPTS or (source is not None and row.source != source)):
        return None
    return row
