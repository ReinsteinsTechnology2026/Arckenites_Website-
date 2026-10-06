"""Outbound email via Microsoft Graph's sendMail, using the OAuth2
client-credentials flow (application permissions) against an Entra app
registration — the production-correct path when Basic SMTP auth is
unavailable (Microsoft Entra Security Defaults enabled, or SMTP AUTH
disabled for the mailbox), which is exactly this project's situation.

Token acquisition uses `msal.ConfidentialClientApplication`, Microsoft's
own maintained library, rather than a hand-rolled OAuth2 implementation —
it also handles in-memory token caching internally (a cached, unexpired
token is reused rather than requesting a fresh one on every send), so
"short-lived tokens, not stored unnecessarily" falls out of using the
library as intended rather than requiring extra code here.

Never logs: the client secret, the acquired access token, or the message
body/OTP. On any failure, only a short, secret-free reason is logged.
"""

import email.utils
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx
import msal

from app.config import settings

logger = logging.getLogger("ms_graph_email")

GRAPH_SCOPE = ["https://graph.microsoft.com/.default"]
GRAPH_AUTHORITY_TEMPLATE = "https://login.microsoftonline.com/{tenant_id}"
GRAPH_SEND_MAIL_URL_TEMPLATE = "https://graph.microsoft.com/v1.0/users/{sender}/sendMail"

# One shared MSAL app instance (not one per call) so its internal token
# cache is actually reused across sends. Built lazily — module import must
# never fail just because Graph isn't configured (e.g. local dev).
_msal_app: msal.ConfidentialClientApplication | None = None


def _get_msal_app() -> msal.ConfidentialClientApplication:
    global _msal_app
    if _msal_app is None:
        _msal_app = msal.ConfidentialClientApplication(
            client_id=settings.ms_graph_client_id,
            client_credential=settings.ms_graph_client_secret,
            authority=GRAPH_AUTHORITY_TEMPLATE.format(tenant_id=settings.ms_graph_tenant_id),
        )
    return _msal_app


def _acquire_token() -> str | None:
    """Returns an access token, or None on failure. Only the OAuth error
    *code* (a short machine-readable string like "invalid_client") is ever
    logged — never `error_description` (verbose free text from Entra) and
    never the token/secret themselves."""
    app = _get_msal_app()

    # Uses MSAL's own cache first; only calls out to Entra when there is no
    # cached, still-valid token for this scope.
    result = app.acquire_token_silent(GRAPH_SCOPE, account=None)
    if not result:
        result = app.acquire_token_for_client(scopes=GRAPH_SCOPE)

    if not result or "access_token" not in result:
        error_code = (result or {}).get("error", "unknown_error")
        logger.error("Microsoft Graph token acquisition failed error=%s", error_code)
        return None

    return result["access_token"]


# HTTP statuses that mean "not accepted, try again later" rather than "this
# message is wrong". Graph throttles with 429 and reports overload with 503;
# the gateway codes are transient for the same reason.
RETRYABLE_STATUS_CODES = frozenset({429, 502, 503, 504})

# Retry-After values above this are clamped; a longer wait is not useful to a
# background worker and would strand the delivery.
MAX_RETRY_AFTER_SECONDS = 6 * 60 * 60


@dataclass(frozen=True)
class GraphSendResult:
    """Outcome of one sendMail call.

    accepted             Graph returned 202: the message was queued.
    retryable            The message was not accepted and it is safe to try
                         again later (throttled, overloaded, or never reached
                         Graph). Retrying cannot duplicate an email.
    retry_after_seconds  Graph's Retry-After, when it was given.
    error_code           A short, secret-free category for logs and the
                         delivery record. Never a message body or address.
    """

    accepted: bool
    retryable: bool = False
    retry_after_seconds: int | None = None
    error_code: str | None = None


def _parse_retry_after(value: str | None) -> int | None:
    """Retry-After is either delta-seconds or an HTTP date (RFC 9110)."""
    if not value:
        return None
    value = value.strip()
    try:
        return min(max(int(value), 0), MAX_RETRY_AFTER_SECONDS)
    except ValueError:
        pass
    try:
        when = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    seconds = int((when - datetime.now(timezone.utc)).total_seconds())
    return min(max(seconds, 0), MAX_RETRY_AFTER_SECONDS)


def send_email_via_graph_result(to_address: str, subject: str, body: str, html_body: str | None = None) -> GraphSendResult:
    """Sends one message through Microsoft Graph and classifies the outcome.

    Retryable: token acquisition failed (no request was sent), the connection
    could not be opened (no request was sent), or Graph answered 429/502/503/504.

    Not retryable: any other HTTP error (for example an invalid recipient).

    Not retried, because the request may already have been accepted: a read
    or write timeout after the request was sent. Retrying could send the same
    email twice, which is worse than one unconfirmed delivery.
    """
    token = _acquire_token()
    if token is None:
        return GraphSendResult(accepted=False, retryable=True, error_code="token_unavailable")

    url = GRAPH_SEND_MAIL_URL_TEMPLATE.format(sender=settings.ms_graph_sender_email)
    content_type, content = ("HTML", html_body) if html_body is not None else ("Text", body)
    payload = {
        "message": {
            "subject": subject,
            "body": {"contentType": content_type, "content": content},
            "toRecipients": [{"emailAddress": {"address": to_address}}],
        },
        "saveToSentItems": False,
    }

    try:
        response = httpx.post(
            url,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=payload,
            timeout=10,
        )
    except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
        # The connection was never established, so Graph never saw the request.
        logger.error("Microsoft Graph sendMail not attempted error_type=%s", type(exc).__name__)
        return GraphSendResult(accepted=False, retryable=True, error_code="connect_failed")
    except httpx.HTTPError as exc:
        logger.error("Microsoft Graph sendMail request failed error_type=%s", type(exc).__name__)
        return GraphSendResult(accepted=False, retryable=False, error_code="delivery_unknown")

    if response.status_code == 202:
        return GraphSendResult(accepted=True)

    # Graph error responses are JSON like {"error": {"code": "...",
    # "message": "..."}} — the "message" text can echo back request
    # details (e.g. a malformed recipient), so only the numeric status and
    # the short error code are logged, never the full body.
    error_code = "unknown"
    try:
        error_code = response.json().get("error", {}).get("code", "unknown")
    except Exception:
        pass
    logger.error("Microsoft Graph sendMail failed status=%s error_code=%s", response.status_code, error_code)

    if response.status_code in RETRYABLE_STATUS_CODES:
        retry_after = _parse_retry_after(response.headers.get("Retry-After"))
        return GraphSendResult(
            accepted=False, retryable=True, retry_after_seconds=retry_after,
            error_code=f"http_{response.status_code}",
        )
    return GraphSendResult(accepted=False, retryable=False, error_code=str(error_code)[:64])


def send_email_via_graph(to_address: str, subject: str, body: str, html_body: str | None = None) -> bool:
    """Boolean form used by the OTP and notification emails. Behaviour is
    unchanged: True only once Graph has accepted the request (HTTP 202)."""
    return send_email_via_graph_result(to_address, subject, body, html_body).accepted
