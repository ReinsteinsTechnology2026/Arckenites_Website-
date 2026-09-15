"""Outbound email — the delivery mechanism behind the student/staff
notification on/off toggle (see app/core/notify.py for the actual "should
this event email this person" decisions). Uses plain smtplib against Gmail
SMTP (or any other SMTP host) — no third-party SDK needed for this volume.
"""

import logging
import smtplib
from email.message import EmailMessage

from app.config import settings

logger = logging.getLogger("email")


def send_email(to_address: str, subject: str, body: str) -> bool:
    """Best-effort send — returns False (and logs) instead of raising, so a
    notification email failure never breaks the request that triggered it
    (a chat message, a support reply, etc. must still succeed either way)."""
    if not to_address:
        return False
    if not settings.smtp_username or not settings.smtp_password or not settings.smtp_from_address:
        logger.warning("SMTP not configured — skipping email to %s: %s", to_address, subject)
        return False

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = f"{settings.smtp_from_name} <{settings.smtp_from_address}>"
    msg["To"] = to_address
    msg.set_content(body)

    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as server:
            server.starttls()
            server.login(settings.smtp_username, settings.smtp_password)
            server.send_message(msg)
        return True
    except Exception:
        logger.exception("Failed to send email to %s", to_address)
        return False
