"""The student/staff notification on/off toggle, applied. Every call site
across the app funnels through `notify_user()`, which is the single place
that checks "does this person want email notifications, and do we have an
address for them" before anything is actually sent — callers don't need to
re-check either.
"""

from app.core.email import send_email
from app.models.user import RoleEnum, User


def notifications_enabled(user: User) -> bool:
    if user.role == RoleEnum.student:
        return bool(user.student_profile and user.student_profile.email_notifications_enabled)
    if user.role == RoleEnum.staff:
        return bool(user.staff_profile and user.staff_profile.email_notifications_enabled)
    return False  # admins aren't part of this feature


def notify_user(user: User, subject: str, body: str) -> None:
    """Best-effort — never raises, so a notification email is never allowed
    to break the request that triggered it."""
    if not notifications_enabled(user):
        return
    address = user.email  # resolves to student_profile/staff_profile.email
    if not address:
        return
    try:
        send_email(address, f"Arckenites — {subject}", body)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Per-event helpers — one per notification category, so call sites read as
# "notify_new_chat_message(...)" instead of hand-building subject/body text
# inline at every trigger point.
# ---------------------------------------------------------------------------

def notify_new_chat_message(recipient: User, sender_name: str, preview: str) -> None:
    notify_user(
        recipient,
        f"New message from {sender_name}",
        f"{sender_name} sent you a message on Arckenites:\n\n\"{preview}\"\n\nLog in to reply: https://arckenites.com",
    )


def notify_support_activity(recipient: User, ticket_subject: str, event: str) -> None:
    notify_user(
        recipient,
        f"Support ticket update — {ticket_subject}",
        f"{event}\n\nTicket: {ticket_subject}\n\nLog in to view: https://arckenites.com",
    )


def notify_batch_update(recipient: User, batch_name: str, event: str) -> None:
    notify_user(
        recipient,
        f"{batch_name} — {event}",
        f"{event}\n\nBatch: {batch_name}\n\nLog in for details: https://arckenites.com",
    )


def notify_lab_access_change(recipient: User, status: str, reason: str | None) -> None:
    notify_user(
        recipient,
        f"Lab access {status.lower()}",
        f"Your lab access has been {status.lower()} by an administrator."
        + (f"\n\nReason: {reason}" if reason else "")
        + "\n\nLog in to check your current status: https://arckenites.com",
    )
