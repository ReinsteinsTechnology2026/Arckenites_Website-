"""Community announcements/updates: publishing, eligible-recipient selection,
duplicate-safe email queueing, and the background delivery worker.

Flow:
  1. An admin publishes a post (api/routes_admin_community_posts.py).
     In the SAME transaction that marks it published, one 'pending'
     CommunityPostEmailDelivery row is inserted per eligible member. The
     unique (post_id, user_id) constraint makes re-publishing, retried
     requests, and restarts unable to queue a second email to the same person.
  2. A background asyncio worker (started at app startup) claims pending
     rows in small batches and sends each one through the shared
     core/email.py transport (Microsoft Graph in production). The admin's
     request never waits on email.
  3. Each row's outcome is recorded individually, so one recipient's
     failure cannot affect the published post or any other recipient.

Eligibility is re-checked at send time, not only at publish time, so a
member disabled between publish and send is skipped rather than emailed.

Never logs email addresses, message bodies, or provider responses.
"""

import asyncio
import html
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select, update
from sqlalchemy.dialects.postgresql import insert

from app.config import settings
from app.core.email import SendResult, send_email_detailed
from app.database import SessionLocal
from app.models.community import CommunityProfile
from app.models.community_post import (
    CommunityPost,
    CommunityPostEmailDelivery,
    CommunityPostEmailStatus,
    CommunityPostKind,
    CommunityPostStatus,
)
from app.models.user import RoleEnum, User

logger = logging.getLogger("community_posts")

# A row still 'sending' after this long means the worker died mid-send. It is
# reconciled to 'failed' rather than retried, because the email may already
# have been accepted: resending could duplicate it, which we must never do.
STALE_CLAIM_AFTER = timedelta(minutes=10)
BATCH_SIZE = 50

# Recipients inserted per INSERT statement. Each row binds 3 parameters, so a
# chunk of 500 uses 1,500 of PostgreSQL's 65,535 bind-parameter limit.
ENQUEUE_CHUNK_SIZE = 500

# Retry policy for transient provider failures. Attempt 1 waits 60s, then
# 120s, 240s, ... capped at one hour. After MAX_SEND_ATTEMPTS the recipient is
# marked failed. The total wait across all attempts is roughly two hours.
RETRY_BASE_SECONDS = 60
RETRY_CAP_SECONDS = 60 * 60
MAX_SEND_ATTEMPTS = 8

# When no transport is configured, nothing is attempted and the delivery is
# simply rechecked on this interval. It does not consume an attempt.
TRANSPORT_RECHECK_SECONDS = 5 * 60


def retry_delay_seconds(attempts_made: int, retry_after: int | None) -> int:
    """Seconds to wait after `attempts_made` failed attempts. Graph's
    Retry-After can lengthen the wait, but never shorten the backoff."""
    backoff = min(RETRY_BASE_SECONDS * (2 ** max(attempts_made - 1, 0)), RETRY_CAP_SECONDS)
    return max(backoff, retry_after or 0)


def is_eligible_recipient(user: User | None, profile: CommunityProfile | None) -> bool:
    """A Community user may receive Community emails only when ALL hold:
    role is community, the account is active, and a verified email exists.
    Deleted accounts have no User row at all, so they never reach here."""
    if user is None or profile is None:
        return False
    return (
        user.role == RoleEnum.community
        and user.is_active
        and profile.email_verified_at is not None
        and bool(profile.email)
    )


def _eligible_user_ids(db) -> list[int]:
    rows = db.execute(
        select(User, CommunityProfile)
        .join(CommunityProfile, CommunityProfile.user_id == User.id)
        .where(User.role == RoleEnum.community, User.is_active.is_(True))
    ).all()
    return [user.id for user, profile in rows if is_eligible_recipient(user, profile)]


def enqueue_deliveries_for_post(db, post: CommunityPost) -> int:
    """Insert one pending delivery per eligible member for this post. Must be
    called inside the same transaction that sets the post to published, and
    is safe to call more than once: ON CONFLICT DO NOTHING means existing
    rows (sent, failed, or pending) are never reset or duplicated.

    Inserted in chunks of ENQUEUE_CHUNK_SIZE so a very large community cannot
    exceed PostgreSQL's bind-parameter limit in a single statement. The
    result is the same as one insert: the same rows, and the same count.
    Returns the number of rows newly inserted."""
    user_ids = _eligible_user_ids(db)
    inserted = 0
    for start in range(0, len(user_ids), ENQUEUE_CHUNK_SIZE):
        chunk = user_ids[start:start + ENQUEUE_CHUNK_SIZE]
        # RETURNING reports exactly which rows were inserted; cursor.rowcount
        # is not reliable for multi-row ON CONFLICT inserts.
        rows = db.execute(
            insert(CommunityPostEmailDelivery)
            .values([
                {"post_id": post.id, "user_id": uid, "status": CommunityPostEmailStatus.pending}
                for uid in chunk
            ])
            .on_conflict_do_nothing(constraint="uq_community_post_delivery_post_user")
            .returning(CommunityPostEmailDelivery.id)
        ).all()
        inserted += len(rows)
    return inserted


def _section_anchor(kind: CommunityPostKind) -> str:
    return "announcements" if kind == CommunityPostKind.announcement else "updates"


def build_post_email(post: CommunityPost) -> tuple[str, str, str]:
    """Returns (subject, plain_text, html). Every user-supplied value is
    HTML-escaped. The message never contains IDs, tokens, or internal data —
    only the title, body, a fixed publisher line, the publish date, and a
    link to the Community section where the item appears."""
    kind_label = "Announcement" if post.kind == CommunityPostKind.announcement else "Update"
    published = post.published_at or datetime.now(timezone.utc)
    published_on = published.strftime("%d %B %Y")
    link = f"{settings.public_site_url.rstrip('/')}/community.html#{_section_anchor(post.kind)}"
    subject = f"New {kind_label.lower()} from Arckenites Community: {post.title}"

    footer = (
        "You are receiving this email because you are a verified member of the Arckenites Community.\n"
        "Arckenites Hub"
    )
    text = (
        f"Arckenites Hub\nCommunity\n\n"
        f"[{kind_label}]\n\n{post.title}\n\n{post.body}\n\n"
        f"Published by:\nArckenites Community\n\n"
        f"Published on:\n{published_on}\n\n"
        f"View on Arckenites Community: {link}\n\n{footer}\n"
    )

    body_html = html.escape(post.body).replace("\n", "<br>")
    html_body = f"""<!doctype html>
<html><body style="margin:0;padding:24px;background:#f3f3f6;font-family:Poppins,Arial,sans-serif;">
  <div style="max-width:580px;margin:0 auto;background:#ffffff;border-radius:14px;overflow:hidden;">
    <div style="background:#15151a;padding:22px 28px;">
      <div style="font-size:20px;font-weight:700;color:#ffffff;">Arckenites Hub</div>
      <div style="font-size:12px;font-weight:600;letter-spacing:.12em;text-transform:uppercase;color:#ff6e30;margin-top:4px;">Community</div>
    </div>
    <div style="padding:28px;">
      <div style="font-size:12px;font-weight:600;letter-spacing:.1em;text-transform:uppercase;color:#ff6e30;">{kind_label}</div>
      <h1 style="font-size:22px;line-height:1.3;color:#15151a;margin:8px 0 16px;">{html.escape(post.title)}</h1>
      <div style="font-size:15px;line-height:1.65;color:#333333;">{body_html}</div>
      <div style="margin:26px 0 0;font-size:13px;color:#666666;line-height:1.6;">
        <div><strong style="color:#15151a;">Published by:</strong><br>Arckenites Community</div>
        <div style="margin-top:10px;"><strong style="color:#15151a;">Published on:</strong><br>{published_on}</div>
      </div>
      <div style="margin-top:26px;">
        <a href="{html.escape(link)}" style="display:inline-block;background:#ff6e30;color:#ffffff;text-decoration:none;font-weight:600;padding:12px 22px;border-radius:999px;">View on Arckenites Community</a>
      </div>
    </div>
    <div style="padding:18px 28px;background:#f7f7f9;font-size:12px;color:#777777;line-height:1.6;">
      You are receiving this email because you are a verified member of the Arckenites Community.<br>
      <strong style="color:#15151a;">Arckenites Hub</strong>
    </div>
  </div>
</body></html>"""
    return subject, text, html_body


def _deliver_one(delivery_id: int) -> str:
    """Attempts one claimed delivery and records the outcome. Returns one of:
    sent, failed, skipped, retry, unavailable, or noop. Each row has its own
    session and commit, so progress survives a crash partway through a batch."""
    db = SessionLocal()
    try:
        delivery = db.get(CommunityPostEmailDelivery, delivery_id)
        if delivery is None or delivery.status != CommunityPostEmailStatus.sending:
            return "noop"

        post = db.get(CommunityPost, delivery.post_id)
        user = db.get(User, delivery.user_id)
        profile = user.community_profile if user else None

        if post is None or post.status != CommunityPostStatus.published or not is_eligible_recipient(user, profile):
            delivery.status = CommunityPostEmailStatus.skipped
            delivery.error_code = "ineligible"
            delivery.next_attempt_at = None
            db.commit()
            return "skipped"

        subject, text, html_body = build_post_email(post)
        # Address is read here, used once, and never stored or logged.
        try:
            result = send_email_detailed(profile.email, subject, text, html_body)
        except Exception as exc:
            # The transport raised, so it may have accepted the message before
            # failing. Retrying could send a duplicate, so this is final.
            logger.error("community post email transport raised error_type=%s", type(exc).__name__)
            result = SendResult(accepted=False, error_code="transport_error")

        now = datetime.now(timezone.utc)
        delivery.claimed_at = None

        if result.accepted:
            delivery.status = CommunityPostEmailStatus.sent
            delivery.sent_at = now
            delivery.error_code = None
            delivery.next_attempt_at = None
            db.commit()
            return "sent"

        if result.transport_unavailable:
            # Nothing was attempted. Leave the recipient pending so the email
            # goes out once configuration is restored. Attempts are untouched.
            delivery.status = CommunityPostEmailStatus.pending
            delivery.error_code = result.error_code
            delivery.next_attempt_at = now + timedelta(seconds=TRANSPORT_RECHECK_SECONDS)
            db.commit()
            return "unavailable"

        delivery.attempts += 1
        if result.retryable and delivery.attempts < MAX_SEND_ATTEMPTS:
            delivery.status = CommunityPostEmailStatus.pending
            delivery.error_code = result.error_code
            delivery.next_attempt_at = now + timedelta(
                seconds=retry_delay_seconds(delivery.attempts, result.retry_after_seconds)
            )
            db.commit()
            return "retry"

        delivery.status = CommunityPostEmailStatus.failed
        delivery.error_code = "retries_exhausted" if result.retryable else (result.error_code or "rejected")
        delivery.next_attempt_at = None
        db.commit()
        return "failed"
    finally:
        db.close()


def deliver_pending_batch_once() -> dict[str, int]:
    """One pass: reconcile stale claims, claim up to BATCH_SIZE pending rows
    that are due, and attempt each. Synchronous; the async worker runs it in a
    thread so the event loop (and every request) is never blocked by SMTP or
    Graph I/O. One recipient's outcome never affects another's."""
    counts = {"sent": 0, "failed": 0, "skipped": 0, "retry": 0, "unavailable": 0}
    now = datetime.now(timezone.utc)

    db = SessionLocal()
    try:
        db.execute(
            update(CommunityPostEmailDelivery)
            .where(
                CommunityPostEmailDelivery.status == CommunityPostEmailStatus.sending,
                CommunityPostEmailDelivery.claimed_at < now - STALE_CLAIM_AFTER,
            )
            .values(status=CommunityPostEmailStatus.failed, error_code="interrupted", claimed_at=None)
        )
        due = or_(
            CommunityPostEmailDelivery.next_attempt_at.is_(None),
            CommunityPostEmailDelivery.next_attempt_at <= now,
        )
        claimed_ids = list(db.scalars(
            select(CommunityPostEmailDelivery.id)
            .where(CommunityPostEmailDelivery.status == CommunityPostEmailStatus.pending, due)
            .order_by(CommunityPostEmailDelivery.id)
            .limit(BATCH_SIZE)
            .with_for_update(skip_locked=True)
        ).all())
        if claimed_ids:
            db.execute(
                update(CommunityPostEmailDelivery)
                .where(CommunityPostEmailDelivery.id.in_(claimed_ids))
                .values(status=CommunityPostEmailStatus.sending, claimed_at=now)
            )
        db.commit()
    finally:
        db.close()

    for delivery_id in claimed_ids:
        outcome = _deliver_one(delivery_id)
        if outcome in counts:
            counts[outcome] += 1
    return counts


async def run_community_post_email_worker(interval_sec: int = 20) -> None:
    """Started once at app startup. Drains the pending queue, then sleeps.
    Survives restarts because the queue lives in the database, not in
    memory: anything not yet sent is simply picked up on the next pass."""
    while True:
        try:
            while True:
                counts = await asyncio.to_thread(deliver_pending_batch_once)
                if not any(counts.values()):
                    break
                logger.info(
                    "community post email batch sent=%d retry=%d failed=%d skipped=%d unavailable=%d",
                    counts["sent"], counts["retry"], counts["failed"], counts["skipped"], counts["unavailable"],
                )
        except Exception as exc:
            # Type only: a traceback could echo request or provider details.
            logger.error("community post email worker pass failed error_type=%s", type(exc).__name__)
        await asyncio.sleep(interval_sec)


def delivery_summary(statuses: list[CommunityPostEmailStatus]) -> str:
    """Admin-facing rollup of one post's delivery rows:
    No recipients / Pending / Sent / Partial / Failed. Skipped rows (a member
    who stopped being eligible before send) are left out of the rollup."""
    statuses = [s for s in statuses if s != CommunityPostEmailStatus.skipped]
    if not statuses:
        return "No recipients"
    if any(s in (CommunityPostEmailStatus.pending, CommunityPostEmailStatus.sending) for s in statuses):
        return "Pending"
    sent = sum(1 for s in statuses if s == CommunityPostEmailStatus.sent)
    if sent == len(statuses):
        return "Sent"
    if sent == 0:
        return "Failed"
    return "Partial"
