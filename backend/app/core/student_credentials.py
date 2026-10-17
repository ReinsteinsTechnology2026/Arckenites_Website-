"""Student welcome email. Passwords live only in the request/send process.

The Community announcement queue persists content and cannot safely hold
credentials. Use the shared transport and a verified resend instead.
"""
import html
import logging

from app.config import settings
from app.core.email import send_email_detailed
from app.schemas.admin_students import CredentialEmailOut

logger = logging.getLogger("student_credentials")


def send_student_credentials(*, student_id: int, full_name: str, email: str,
                             temp_password: str, program_name: str) -> CredentialEmailOut:
    portal_url = f"{settings.public_site_url.rstrip('/')}/login.html"
    subject = "Welcome to Arckenites \u2013 Your Student Portal Login Credentials"
    body = (
        f"Dear {full_name},\n\nWelcome to Arckenites.\n\n"
        "Your student account has been successfully created.\n\n"
        f"Name: {full_name}\nEmail: {email}\nTemporary Password: {temp_password}\nProgram: {program_name}\n\n"
        f"Student Portal:\n{portal_url}\n\n"
        "Please use your email address and temporary password to log in.\n"
        "For security, you will be asked to change your password after logging in.\n\n"
        "Regards,\nArckenites\nIT Training & Technology"
    )
    fields = "".join(
        f'<tr><th align="left" style="padding:8px 12px">{label}</th>'
        f'<td style="padding:8px 12px;white-space:pre-wrap">{html.escape(value)}</td></tr>'
        for label, value in (("Name", full_name), ("Email", email),
                             ("Temporary Password", temp_password), ("Program", program_name))
    )
    html_body = (
        '<div style="font-family:Arial,sans-serif;color:#202020;max-width:600px;margin:auto">'
        '<div style="background:#171717;color:#ff641f;padding:20px;font-size:24px;font-weight:bold">Arckenites</div>'
        f'<div style="padding:24px"><p>Dear {html.escape(full_name)},</p>'
        '<p>Welcome to Arckenites.</p><p>Your student account has been successfully created.</p>'
        f'<table style="background:#f5f5f5;border-radius:8px;width:100%">{fields}</table>'
        f'<p>Student Portal: <a href="{html.escape(portal_url, quote=True)}">{html.escape(portal_url)}</a></p>'
        '<p>Please use your email address and temporary password to log in. '
        'For security, you will be asked to change your password after logging in.</p>'
        '<p>Regards,<br><strong>Arckenites</strong><br>IT Training &amp; Technology</p></div></div>'
    )
    try:
        sent = send_email_detailed(email, subject, body, html_body).accepted
    except Exception as exc:
        # Providers can echo message content: never log exception text.
        logger.error("Student credential email failed student_id=%s error_type=%s", student_id, type(exc).__name__)
        sent = False
    if not sent:
        logger.warning("Student credential email not accepted student_id=%s", student_id)
    return CredentialEmailOut(
        credential_email_sent=sent,
        detail=(f"Student created successfully and credentials were emailed to {email}." if sent else
                "Student created successfully, but the credential email could not be sent. "
                "Use Resend Credentials with the original temporary password to retry."),
    )
