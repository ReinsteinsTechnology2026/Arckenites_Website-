"""Trainer welcome email; plaintext credentials exist only in the request/send path."""
import html
import logging

from app.core.email import send_email_detailed
from app.schemas.admin_students import CredentialEmailOut

logger = logging.getLogger("trainer_credentials")
TRAINER_LOGIN_URL = "https://www.arckenites.com/login.html"


def send_trainer_credentials(*, trainer_id: int, full_name: str, email: str,
                             temp_password: str) -> CredentialEmailOut:
    subject = "Welcome to Arckenites – Your Trainer Portal Login Credentials"
    body = (
        f"Dear {full_name},\n\nYour Arckenites trainer account has been created.\n\n"
        f"Login ID: {email}\nTemporary Password: {temp_password}\n"
        f"Login URL: {TRAINER_LOGIN_URL}\n\n"
        "Sign in and change your temporary password when prompted.\n\n"
        "Regards,\nArckenites\nIT Training & Technology"
    )
    fields = "".join(
        f'<tr><th align="left" style="padding:8px 12px">{label}</th>'
        f'<td style="padding:8px 12px;white-space:pre-wrap">{html.escape(value)}</td></tr>'
        for label, value in (("Login ID", email), ("Temporary Password", temp_password))
    )
    html_body = (
        '<div style="font-family:Arial,sans-serif;color:#202020;max-width:600px;margin:auto">'
        '<div style="background:#171717;color:#ff641f;padding:20px;font-size:24px;font-weight:bold">Arckenites</div>'
        f'<div style="padding:24px"><p>Dear {html.escape(full_name)},</p>'
        '<p>Your Arckenites trainer account has been created.</p>'
        f'<table style="background:#f5f5f5;border-radius:8px;width:100%">{fields}</table>'
        f'<p>Login URL: <a href="{html.escape(TRAINER_LOGIN_URL, quote=True)}">'
        f'{html.escape(TRAINER_LOGIN_URL)}</a></p>'
        '<p>Sign in and change your temporary password when prompted.</p>'
        '<p>Regards,<br><strong>Arckenites</strong><br>IT Training &amp; Technology</p></div></div>'
    )
    try:
        sent = send_email_detailed(email, subject, body, html_body).accepted
    except Exception as exc:
        # Providers may echo request content in exception text.
        logger.error("Trainer credential email failed trainer_id=%s error_type=%s",
                     trainer_id, type(exc).__name__)
        sent = False
    if not sent:
        logger.warning("Trainer credential email not accepted trainer_id=%s", trainer_id)
    return CredentialEmailOut(
        credential_email_sent=sent,
        detail=(f"Trainer created successfully and credentials were emailed to {email}." if sent else
                "Trainer created successfully, but the credential email could not be sent. "
                "Use Resend Credentials with the original temporary password to retry."),
    )
