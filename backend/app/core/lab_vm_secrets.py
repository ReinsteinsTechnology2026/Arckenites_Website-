"""Encryption-at-rest and generation for VM Lab Access's rotating RDP
passwords — deliberately its own module, separate from the human password
hashing in core/security.py, because this secret must be *recoverable*
(the student needs to read it, the agent needs to apply it), not just
verifiable like a login password hash.

No new required deployment secret: the Fernet key is derived from the
existing JWT_SECRET (already required in every environment) via SHA-256,
namespaced with a fixed string so it's never the same bytes JWT signing
uses. This avoids a new required .env value that could break production
startup on a server whose .env this codebase can't reach to update
directly. The password itself is short-lived and auto-rotated on every
grant/revoke/expiry, so this key does not need to protect a long-lived
secret — only the (very short) window between a password being set and
it being rotated away.
"""

import base64
import hashlib
import secrets
import string

from cryptography.fernet import Fernet, InvalidToken

from app.config import settings

_KEY_NAMESPACE = "arckenites-lab-vm-rdp-password-v1"


def _fernet() -> Fernet:
    derived = hashlib.sha256(f"{_KEY_NAMESPACE}:{settings.jwt_secret}".encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(derived))


def encrypt_rdp_password(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_rdp_password(ciphertext: str) -> str | None:
    """Returns None instead of raising on a corrupt/foreign value — a
    password field should never be able to 500 a status page; the caller
    treats None the same as "no password currently available"."""
    try:
        return _fernet().decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        return None


def generate_windows_compliant_password(length: int = 16) -> str:
    """A random password satisfying Windows' default complexity policy
    (at least 3 of: uppercase, lowercase, digit, symbol; minimum length),
    generated with `secrets` (cryptographically random), not `random`."""
    upper, lower, digits = string.ascii_uppercase, string.ascii_lowercase, string.digits
    symbols = "!@#$%^&*()-_=+"
    all_chars = upper + lower + digits + symbols

    required = [secrets.choice(upper), secrets.choice(lower), secrets.choice(digits), secrets.choice(symbols)]
    remainder = [secrets.choice(all_chars) for _ in range(max(length - len(required), 0))]
    chars = required + remainder
    secrets.SystemRandom().shuffle(chars)
    return "".join(chars)
