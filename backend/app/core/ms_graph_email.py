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

import logging

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


def send_email_via_graph(to_address: str, subject: str, body: str) -> bool:
    """Sends a plain-text email as settings.ms_graph_sender_email via
    Microsoft Graph's sendMail. Returns True only once Graph has accepted
    the request (HTTP 202) — never assumes success."""
    token = _acquire_token()
    if token is None:
        return False

    url = GRAPH_SEND_MAIL_URL_TEMPLATE.format(sender=settings.ms_graph_sender_email)
    payload = {
        "message": {
            "subject": subject,
            "body": {"contentType": "Text", "content": body},
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
    except httpx.HTTPError as exc:
        logger.error("Microsoft Graph sendMail request failed error_type=%s", type(exc).__name__)
        return False

    if response.status_code == 202:
        return True

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
    return False
