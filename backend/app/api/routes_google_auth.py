"""Google sign-in routes for existing Arckenites Portal accounts. See
core/google_auth.py for the flow and the security controls.

GET  /api/auth/google/login      starts sign-in (redirect to Google)
GET  /api/auth/google/callback   Google returns here; success or failure is
                                 reported back to the login page
POST /api/auth/google/exchange   the login page trades its one-time ticket for
                                 the same session token password login returns

Failures never say whether an address has a portal account beyond what the
short category needs, and never reveal any Google or account data.
"""

import hmac
import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.api.routes_auth import begin_or_finish_login
from app.config import settings
from app.core import google_auth
from app.core.google_auth import GoogleAuthError
from app.core.rate_limit import limiter
from app.crud.audit import write_audit_event
from app.crud.system_settings import get_settings
from app.database import get_db
from app.models.audit_log import AuthEventType
from app.models.google_identity import GoogleLoginTicket
from app.models.user import RoleEnum, User
from app.schemas.auth import LoginResponse, MfaChallengeOut

router = APIRouter(prefix="/auth/google", tags=["auth-google"])

INVALID_TICKET_ERROR = "This sign-in link has expired. Please try again."
UNAVAILABLE_ERROR = "Google sign-in is not available right now."


class GoogleExchangeRequest(BaseModel):
    ticket: str = Field(min_length=20, max_length=200)
    portal: str | None = Field(default=None, pattern="^(student|staff)$")


def _client_meta(request: Request) -> tuple[str, str]:
    ip = request.client.host if request.client else "unknown"
    ua = request.headers.get("user-agent", "unknown")
    return ip, ua


def _secure_cookies() -> bool:
    # Cookies are sent over HTTPS only everywhere except local development.
    return settings.env != "development"


def _login_page_redirect(code: str | None = None, ticket: str | None = None) -> RedirectResponse:
    base = f"{settings.public_site_url.rstrip('/')}/login.html"
    if ticket is not None:
        target = f"{base}?google_ticket={ticket}"
    else:
        target = f"{base}?google_error={code}"
    return RedirectResponse(target, status_code=status.HTTP_302_FOUND)


@router.get("/login")
@limiter.limit("20/minute")
def google_login(request: Request):
    if not settings.google_configured:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=UNAVAILABLE_ERROR)

    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    response = RedirectResponse(google_auth.authorization_url(state, nonce), status_code=status.HTTP_302_FOUND)
    response.set_cookie(
        key=google_auth.STATE_COOKIE,
        value=google_auth.make_state_cookie(state, nonce),
        max_age=int(google_auth.STATE_TTL.total_seconds()),
        httponly=True,
        secure=_secure_cookies(),
        samesite="lax",
        path=google_auth.STATE_COOKIE_PATH,
    )
    return response


@router.get("/callback")
@limiter.limit("30/minute")
def google_callback(
    request: Request,
    db: Session = Depends(get_db),
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
):
    ip, ua = _client_meta(request)
    state_cookie = request.cookies.get(google_auth.STATE_COOKIE)

    try:
        if not settings.google_configured:
            raise GoogleAuthError("unavailable")
        if error is not None:
            # The visitor cancelled, or Google reported an error. Nothing to exchange.
            raise GoogleAuthError("cancelled")
        if not state_cookie or not state:
            raise GoogleAuthError("state")

        expected_state, nonce = google_auth.read_state_cookie(state_cookie)
        if not hmac.compare_digest(expected_state, state):
            raise GoogleAuthError("state")
        if not code:
            raise GoogleAuthError("exchange")

        id_token = google_auth.exchange_code(code)
        identity = google_auth.verify_id_token(id_token, nonce)

        maintenance = get_settings(db).maintenance_mode
        user = google_auth.match_portal_account(db, identity["sub"], identity["email"], maintenance)

        raw_ticket, digest = google_auth.new_ticket()
        db.add(GoogleLoginTicket(
            token_hash=digest,
            user_id=user.id,
            expires_at=datetime.now(timezone.utc) + google_auth.TICKET_TTL,
        ))
        db.commit()
        response = _login_page_redirect(ticket=raw_ticket)
    except GoogleAuthError as exc:
        db.rollback()
        write_audit_event(
            db, AuthEventType.login_failure, ip, ua, detail=f"google sign-in: {exc.code}", status="failure",
        )
        response = _login_page_redirect(code=exc.code)

    # The state cookie is single-use: it is cleared whether or not sign-in worked.
    response.delete_cookie(key=google_auth.STATE_COOKIE, path=google_auth.STATE_COOKIE_PATH)
    return response


@router.post("/exchange", response_model=LoginResponse | MfaChallengeOut)
@limiter.limit("20/minute")
def google_exchange(request: Request, payload: GoogleExchangeRequest, db: Session = Depends(get_db)):
    ip, ua = _client_meta(request)
    invalid = HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=INVALID_TICKET_ERROR)

    now = datetime.now(timezone.utc)
    digest = google_auth.hash_ticket(payload.ticket)
    ticket = db.query(GoogleLoginTicket).filter(GoogleLoginTicket.token_hash == digest).first()
    if ticket is None or ticket.used_at is not None or ticket.expires_at < now:
        raise invalid

    # Single use, claimed atomically: a second request with the same ticket
    # finds used_at already set and changes nothing.
    claimed = db.execute(
        update(GoogleLoginTicket)
        .where(GoogleLoginTicket.id == ticket.id, GoogleLoginTicket.used_at.is_(None))
        .values(used_at=now)
    ).rowcount
    if claimed != 1:
        db.rollback()
        raise invalid

    user = db.get(User, ticket.user_id)
    try:
        google_auth.ensure_can_sign_in(user, get_settings(db).maintenance_mode)
    except GoogleAuthError:
        # The account changed (disabled, locked, or maintenance) since the callback.
        db.commit()
        write_audit_event(db, AuthEventType.login_failure, ip, ua, user=user, detail="google ticket for ineligible account")
        raise invalid

    if ((payload.portal == "student" and user.role != RoleEnum.student)
            or (payload.portal == "staff" and user.role not in (RoleEnum.staff, RoleEnum.admin))):
        db.commit()
        write_audit_event(db, AuthEventType.login_failure, ip, ua, user=user, detail="google sign-in wrong portal")
        raise invalid

    db.commit()  # consume the Google exchange ticket before issuing a challenge
    return begin_or_finish_login(db, request, user, "google")
