"""Outbound email — the delivery mechanism behind the student/staff
notification on/off toggle (see app/core/notify.py for the actual "should
this event email this person" decisions) and the Community OTP flow (see
api/routes_community_auth.py).

Two transports, never both for the same message:
  - Microsoft Graph (core/ms_graph_email.py), used whenever
    settings.ms_graph_configured is true — the production path, since it
    works under Microsoft Entra Security Defaults / with SMTP AUTH
    disabled for the mailbox.
  - Plain smtplib SMTP (this file), used only when Graph is NOT
    configured at all (e.g. local dev against a personal Gmail account).
There is no per-message fallback from Graph to SMTP: if Graph is
configured and a send fails, that failure is reported as-is rather than
silently retried over SMTP, which would use a different, unintended
sender identity and auth model.
"""

import logging
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage

from app.config import settings
from app.core.ms_graph_email import send_email_via_graph_result

logger = logging.getLogger("email")


@dataclass(frozen=True)
class SendResult:
    """Detailed outcome of one send, for callers that must tell the difference
    between "nothing is configured" and "the provider refused this message"."""

    accepted: bool
    # Safe to try again later with no risk of a duplicate.
    retryable: bool = False
    retry_after_seconds: int | None = None
    # No outbound transport is configured (or its credentials were rejected
    # at connect). Nothing was attempted; the caller should wait and retry
    # without counting this as a failed attempt.
    transport_unavailable: bool = False
    error_code: str | None = None


def send_email_detailed(to_address: str, subject: str, body: str, html_body: str | None = None) -> SendResult:
    """Sends one email through the active transport and reports why it did or
    did not go. Graph is used whenever it is configured. SMTP is used only
    when Graph is NOT configured; there is never a per-message fallback from
    Graph to SMTP, so a Graph failure is reported as-is."""
    if not to_address:
        return SendResult(accepted=False, error_code="no_address")

    if settings.ms_graph_configured:
        result = send_email_via_graph_result(to_address, subject, body, html_body)
        return SendResult(
            accepted=result.accepted,
            retryable=result.retryable,
            retry_after_seconds=result.retry_after_seconds,
            error_code=result.error_code,
        )

    if not settings.smtp_username or not settings.smtp_password or not settings.smtp_from_address:
        logger.warning("Neither Microsoft Graph nor SMTP is configured — skipping email to %s: %s", to_address, subject)
        return SendResult(accepted=False, transport_unavailable=True, error_code="transport_unavailable")

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = f"{settings.smtp_from_name} <{settings.smtp_from_address}>"
    msg["To"] = to_address
    msg.set_content(body)
    if html_body is not None:
        msg.add_alternative(html_body, subtype="html")

    # Each stage is caught separately purely for diagnosability. Never logs
    # the message body, the password, or anything from the SMTP server's auth
    # challenge/response beyond its own error class name.
    stage = "CONNECT"
    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as server:
            stage = "STARTTLS"
            server.starttls()
            stage = "AUTH"
            server.login(settings.smtp_username, settings.smtp_password)
            stage = "SEND"
            server.send_message(msg)
        return SendResult(accepted=True)
    except smtplib.SMTPAuthenticationError:
        # A configuration problem, not a problem with this recipient.
        logger.error("Email send to %s failed stage=%s (SMTP server rejected the configured credentials)", to_address, stage)
        return SendResult(accepted=False, transport_unavailable=True, error_code="smtp_auth_rejected")
    except (smtplib.SMTPException, OSError) as exc:
        logger.error("Email send to %s failed stage=%s error_type=%s", to_address, stage, type(exc).__name__)
        return SendResult(accepted=False, retryable=stage in ("CONNECT", "STARTTLS"), error_code="smtp_failed")
    except Exception:
        logger.exception("Email send to %s failed stage=%s (unexpected error)", to_address, stage)
        return SendResult(accepted=False, error_code="smtp_unexpected")


def send_email(to_address: str, subject: str, body: str, html_body: str | None = None) -> bool:
    """Best-effort send — returns False (and logs) instead of raising, so a
    notification email failure never breaks the request that triggered it
    (a chat message, a support reply, etc. must still succeed either way).
    Every caller keeps this exact signature/contract regardless of which
    transport is active underneath. html_body is optional; when omitted the
    message is plain text exactly as before (OTP and notification emails)."""
    return send_email_detailed(to_address, subject, body, html_body).accepted
