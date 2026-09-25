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
from email.message import EmailMessage

from app.config import settings
from app.core.ms_graph_email import send_email_via_graph

logger = logging.getLogger("email")


def send_email(to_address: str, subject: str, body: str) -> bool:
    """Best-effort send — returns False (and logs) instead of raising, so a
    notification email failure never breaks the request that triggered it
    (a chat message, a support reply, etc. must still succeed either way).
    Every caller keeps this exact signature/contract regardless of which
    transport is active underneath."""
    if not to_address:
        return False

    if settings.ms_graph_configured:
        return send_email_via_graph(to_address, subject, body)

    if not settings.smtp_username or not settings.smtp_password or not settings.smtp_from_address:
        logger.warning("Neither Microsoft Graph nor SMTP is configured — skipping email to %s: %s", to_address, subject)
        return False

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = f"{settings.smtp_from_name} <{settings.smtp_from_address}>"
    msg["To"] = to_address
    msg.set_content(body)

    # Each stage is caught separately purely for diagnosability — every
    # branch still returns False the same way, so this is a pure logging
    # improvement, not a behavior change for any existing caller. Never
    # logs the message body, the password, or anything from the SMTP
    # server's auth challenge/response beyond its own error class name.
    stage = "CONNECT"
    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as server:
            stage = "STARTTLS"
            server.starttls()
            stage = "AUTH"
            server.login(settings.smtp_username, settings.smtp_password)
            stage = "SEND"
            server.send_message(msg)
        return True
    except smtplib.SMTPAuthenticationError:
        logger.error("Email send to %s failed stage=%s (SMTP server rejected the configured credentials)", to_address, stage)
        return False
    except (smtplib.SMTPException, OSError) as exc:
        logger.error("Email send to %s failed stage=%s error_type=%s", to_address, stage, type(exc).__name__)
        return False
    except Exception:
        logger.exception("Email send to %s failed stage=%s (unexpected error)", to_address, stage)
        return False
