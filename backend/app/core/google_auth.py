"""Google sign-in for EXISTING Arckenites Portal accounts only (student, staff,
admin). Community accounts never match and never receive a Google session.

Flow (OpenID Connect, authorization code, server-side exchange):
  1. make_state_cookie / authorization_url: a random state and nonce, the
     state carried in a signed, short-lived cookie, then a redirect to Google.
  2. exchange_code: the callback trades the one-time code for an id_token on
     Google's token endpoint, authenticated with the client secret.
  3. verify_id_token: the id_token signature is checked against Google's
     published keys. Issuer, audience, expiry, nonce and email_verified are
     checked too. Browser-supplied identity is never trusted.
  4. match_portal_account: the verified email must match exactly one existing
     portal account. The account must be active, not locked, and allowed
     under maintenance mode. The Google `sub` is linked on first use, and any
     later sign-in must present the same `sub`.
  5. issue_ticket: a one-time ticket is handed to the login page, which
     exchanges it for the same session token that password login returns.

Never logs: the client secret, the authorization code, the id_token, access
or refresh tokens, the state or nonce, or any Google claim beyond the outcome.
"""

import hashlib
import hmac
import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx
import jwt
from jwt import PyJWKClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.core.rate_limit import is_locked
from app.core.security import JWT_ALGORITHM
from app.models.admin_profile import AdminProfile
from app.models.google_identity import UserGoogleIdentity
from app.models.staff import StaffProfile
from app.models.student import StudentProfile
from app.models.user import RoleEnum, User

logger = logging.getLogger("google_auth")

AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
JWKS_URI = "https://www.googleapis.com/oauth2/v3/certs"
ISSUERS = ("https://accounts.google.com", "accounts.google.com")
SCOPES = "openid email profile"

PORTAL_ROLES = (RoleEnum.student, RoleEnum.staff, RoleEnum.admin)

STATE_COOKIE = "ak_google_state"
STATE_COOKIE_PATH = "/api/auth/google"
STATE_TTL = timedelta(minutes=10)
TICKET_TTL = timedelta(seconds=60)
STATE_PURPOSE = "google_state"


class GoogleAuthError(Exception):
    """A sign-in failure. `code` is a short category shown to the user; it never
    carries account details, so it cannot be used to read account data."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


# ---------------- state and redirect ----------------

def authorization_url(state: str, nonce: str) -> str:
    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": settings.google_redirect_uri,
        "response_type": "code",
        "scope": SCOPES,
        "state": state,
        "nonce": nonce,
        "prompt": "select_account",
    }
    return f"{AUTH_ENDPOINT}?{urlencode(params)}"


def make_state_cookie(state: str, nonce: str) -> str:
    payload = {
        "purpose": STATE_PURPOSE,
        "state": state,
        "nonce": nonce,
        "exp": datetime.now(timezone.utc) + STATE_TTL,
        "jti": str(uuid.uuid4()),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=JWT_ALGORITHM)


def read_state_cookie(value: str) -> tuple[str, str]:
    """Returns (state, nonce) from a valid, unexpired state cookie."""
    try:
        payload = jwt.decode(value, settings.jwt_secret, algorithms=[JWT_ALGORITHM])
    except jwt.PyJWTError:
        raise GoogleAuthError("state")
    if payload.get("purpose") != STATE_PURPOSE:
        raise GoogleAuthError("state")
    state, nonce = payload.get("state"), payload.get("nonce")
    if not isinstance(state, str) or not isinstance(nonce, str):
        raise GoogleAuthError("state")
    return state, nonce


# ---------------- Google token exchange and identity verification ----------------

def exchange_code(code: str) -> str:
    """Trades the one-time authorization code for an id_token. Returns only the
    id_token. Nothing from the response is logged."""
    try:
        response = httpx.post(
            TOKEN_ENDPOINT,
            data={
                "code": code,
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "redirect_uri": settings.google_redirect_uri,
                "grant_type": "authorization_code",
            },
            timeout=10,
        )
    except httpx.HTTPError as exc:
        logger.error("Google token exchange unreachable error_type=%s", type(exc).__name__)
        raise GoogleAuthError("exchange")

    if response.status_code != 200:
        error_code = "unknown"
        try:
            error_code = str(response.json().get("error", "unknown"))[:64]
        except Exception:
            pass
        logger.error("Google token exchange rejected status=%s error=%s", response.status_code, error_code)
        raise GoogleAuthError("exchange")

    try:
        id_token = response.json().get("id_token")
    except Exception:
        raise GoogleAuthError("exchange")
    if not isinstance(id_token, str) or not id_token:
        raise GoogleAuthError("exchange")
    return id_token


_jwks_client: PyJWKClient | None = None


def _signing_keys() -> PyJWKClient:
    global _jwks_client
    if _jwks_client is None:
        _jwks_client = PyJWKClient(JWKS_URI, cache_keys=True)
    return _jwks_client


def verify_id_token(id_token: str, expected_nonce: str) -> dict:
    """Verifies the id_token and returns {'sub', 'email'} for a verified Google
    email. Raises GoogleAuthError with a category for any failure."""
    try:
        signing_key = _signing_keys().get_signing_key_from_jwt(id_token).key
        claims = jwt.decode(
            id_token,
            signing_key,
            algorithms=["RS256"],
            audience=settings.google_client_id,
            options={"require": ["exp", "iat", "iss", "aud", "sub"]},
        )
    except jwt.PyJWTError as exc:
        logger.warning("Google id_token rejected error_type=%s", type(exc).__name__)
        raise GoogleAuthError("identity")
    except Exception as exc:
        # Key download failed: the id_token could not be checked at all.
        logger.error("Google signing keys unavailable error_type=%s", type(exc).__name__)
        raise GoogleAuthError("exchange")

    if claims.get("iss") not in ISSUERS:
        raise GoogleAuthError("identity")
    if not hmac.compare_digest(str(claims.get("nonce", "")), expected_nonce):
        raise GoogleAuthError("identity")

    sub = claims.get("sub")
    if not isinstance(sub, str) or not sub:
        raise GoogleAuthError("identity")

    email_verified = claims.get("email_verified")
    if email_verified is not True and str(email_verified).lower() != "true":
        raise GoogleAuthError("unverified")

    email = claims.get("email")
    if not isinstance(email, str) or "@" not in email:
        raise GoogleAuthError("identity")
    return {"sub": sub, "email": email.strip().lower()}


# ---------------- account matching ----------------

def ensure_can_sign_in(user: User | None, maintenance_mode: bool) -> None:
    """The same gates the password login applies, in the same spirit: only
    portal roles, active accounts only, not locked, and maintenance mode blocks
    everyone except admins."""
    if user is None or user.role not in PORTAL_ROLES:
        raise GoogleAuthError("no_account")
    if not user.is_active:
        raise GoogleAuthError("disabled")
    if is_locked(user):
        raise GoogleAuthError("locked")
    if maintenance_mode and user.role != RoleEnum.admin:
        raise GoogleAuthError("maintenance")


def _portal_users_with_email(db: Session, email: str) -> list[User]:
    """Every portal user (student, staff, admin) whose account email equals this
    address. Admin usernames are often the email itself, so the username is
    checked too; the profile emails are the primary source."""
    email = email.strip().lower()
    found: dict[int, User] = {}

    username_matches = db.scalars(
        select(User).where(func.lower(User.username) == email, User.role.in_(PORTAL_ROLES))
    ).all()
    profile_matches = db.scalars(
        select(User).join(StudentProfile, StudentProfile.user_id == User.id)
        .where(func.lower(StudentProfile.email) == email, User.role == RoleEnum.student)
    ).all()
    profile_matches += db.scalars(
        select(User).join(StaffProfile, StaffProfile.user_id == User.id)
        .where(func.lower(StaffProfile.email) == email, User.role == RoleEnum.staff)
    ).all()
    profile_matches += db.scalars(
        select(User).join(AdminProfile, AdminProfile.user_id == User.id)
        .where(func.lower(AdminProfile.email) == email, User.role == RoleEnum.admin)
    ).all()

    for user in list(username_matches) + list(profile_matches):
        found[user.id] = user
    return list(found.values())


def match_portal_account(db: Session, sub: str, email: str, maintenance_mode: bool) -> User:
    """Finds the one portal account this verified Google identity may use.
    Links the Google `sub` on first use. Never creates an account. Raises
    GoogleAuthError on any ambiguity or mismatch."""
    candidates = _portal_users_with_email(db, email)

    if not candidates:
        raise GoogleAuthError("no_account")
    if len(candidates) > 1:
        logger.error("Google sign-in matched more than one portal account; refusing")
        raise GoogleAuthError("ambiguous")

    user = candidates[0]
    ensure_can_sign_in(user, maintenance_mode)

    linked = db.scalar(select(UserGoogleIdentity).where(UserGoogleIdentity.user_id == user.id))
    if linked is None:
        taken = db.scalar(select(UserGoogleIdentity).where(UserGoogleIdentity.google_sub == sub))
        if taken is not None:
            # This Google account is already linked to a different portal account.
            raise GoogleAuthError("identity")
        db.add(UserGoogleIdentity(user_id=user.id, google_sub=sub, linked_email=email))
        db.flush()
    elif not hmac.compare_digest(linked.google_sub, sub):
        # This portal account is linked to a different Google account.
        raise GoogleAuthError("identity")
    return user


# ---------------- one-time tickets ----------------

def new_ticket() -> tuple[str, str]:
    """Returns (raw_ticket, sha256_hex). Only the hash is stored."""
    raw = secrets.token_urlsafe(32)
    return raw, hash_ticket(raw)


def hash_ticket(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
