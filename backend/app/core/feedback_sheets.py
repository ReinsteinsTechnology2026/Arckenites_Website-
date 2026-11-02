"""Mirror inauguration feedback rows into a Google Sheet.

PostgreSQL is the source of truth. A row is saved first; the spreadsheet copy is
written afterwards through a Google Apps Script Web App (see
backend/integrations/google_sheets/FeedbackSheet.gs). A failed or unavailable
sheet never affects the submitter: the row stays PENDING and is retried with
backoff by a background worker until it succeeds or the attempt limit is reached
(then FAILED, never silently SYNCED).

The Apps Script URL and shared secret come from the server environment only. The
spreadsheet id is configured inside the Apps Script, so no request from this app
(or from a browser) can name a different spreadsheet.
"""
import asyncio
import logging
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Callable

import httpx
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal
from app.models.inauguration_feedback import InaugurationFeedback

logger = logging.getLogger("feedback_sheets")
# httpx logs every request URL at INFO. The Apps Script URL is confidential, so keep it out of any INFO log.
logging.getLogger("httpx").setLevel(logging.WARNING)

# Set at application shutdown so a retry pass stops taking new rows instead of holding the process open.
_stop = threading.Event()
# Longest a single retry pass may keep taking rows (each row can wait on the network for a while).
_PASS_BUDGET_SECONDS = 90

PENDING = "PENDING"
SYNCED = "SYNCED"
FAILED = "FAILED"

# First row of the sheet, in order. The Apps Script owns the column mapping; this list
# documents it and is checked against the script by the tests.
SHEET_COLUMNS = [
    "Submission ID",
    "Submitted At",
    "Name",
    "Domain",
    "Other Domain",
    "Overall Session Rating",
    "Session Usefulness",
    "Learned Something New",
    "Favourite Topic",
    "Other Topic",
    "Future Session Attendance",
    "Suggestions",
    "Interested in Course Contact",
    "Contact Number",
    "Google Sheets Sync Status",
]

_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")
_MAX_BACKOFF_SECONDS = 3600
_CONTACT_LABELS = {"yes": "Yes", "maybe": "Maybe", "no": "No"}


class SheetsSyncError(Exception):
    """A sync attempt failed. `code` is a short, data-free label safe to store and log."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def sanitize_cell(value: str | None) -> str:
    """Neutralise spreadsheet formula injection: a leading = + - @ (or tab/CR) gets a
    single-quote prefix so the cell is read as text, including after CSV export."""
    text = "" if value is None else str(value)
    return "'" + text if text.startswith(_FORMULA_PREFIXES) else text


def build_payload(row: InaugurationFeedback) -> dict:
    """The data sent to the Apps Script (without the secret). The contact number is
    included only when the submitter opted in to course contact."""
    opted_in = row.contact_preference in ("yes", "maybe")
    submitted_at = ""
    if row.created_at:
        submitted_at = _aware(row.created_at).astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    return {
        "submission_id": row.submission_id,
        "submitted_at": submitted_at,
        "name": sanitize_cell(row.name),
        "domain": sanitize_cell(row.domain),
        "domain_other": sanitize_cell(row.domain_other),
        "overall_session": sanitize_cell(row.overall_session),
        "usefulness": sanitize_cell(row.usefulness),
        "learned_something": sanitize_cell(row.learned_something),
        "liked_topic": sanitize_cell(row.liked_topic),
        "liked_topic_other": sanitize_cell(row.liked_topic_other),
        "attend_future": sanitize_cell(row.attend_future),
        "suggestion": sanitize_cell(row.suggestion),
        "contact_preference": _CONTACT_LABELS.get(row.contact_preference, ""),
        "contact_number": sanitize_cell(row.contact_number) if opted_in else "",
    }


def post_to_sheet(payload: dict) -> str:
    """POST to the Apps Script Web App. Returns "appended" or "duplicate" (the row was
    already in the sheet, e.g. an earlier attempt succeeded but its reply was lost).
    Raises SheetsSyncError otherwise. Errors never include the URL, secret or any form data."""
    body = {**payload, "secret": settings.feedback_sheets_shared_secret}
    try:
        # Apps Script answers a POST with a redirect to the result page; follow it.
        with httpx.Client(timeout=httpx.Timeout(settings.feedback_sheets_timeout_seconds, connect=5.0), follow_redirects=True) as client:
            response = client.post(settings.feedback_sheets_webapp_url, json=body)
    except httpx.TimeoutException:
        raise SheetsSyncError("timeout") from None
    except httpx.HTTPError:
        raise SheetsSyncError("network_error") from None
    if response.status_code != 200:
        raise SheetsSyncError(f"http_{response.status_code}")
    try:
        data = response.json()
    except ValueError:
        raise SheetsSyncError("bad_response") from None
    if isinstance(data, dict) and data.get("ok") is True and data.get("result") in ("appended", "duplicate"):
        return data["result"]
    error = data.get("error") if isinstance(data, dict) else None
    code = error if isinstance(error, str) and re.fullmatch(r"[a-z_]{1,40}", error) else "unknown"
    raise SheetsSyncError(f"script_{code}")


def backoff_seconds(attempts: int) -> int:
    return min(settings.feedback_sheets_retry_interval_seconds * 2 ** max(attempts - 1, 0), _MAX_BACKOFF_SECONDS)


def sync_row(db: Session, row: InaugurationFeedback,
             sender: Callable[[dict], str] | None = None, now: datetime | None = None) -> str:
    """One sync attempt for one saved row. Never raises for a sync failure; the outcome is
    recorded on the row (SYNCED, PENDING with a retry time, or FAILED when attempts run out)."""
    if row.sheets_sync_status == SYNCED:
        return SYNCED
    sender = sender or post_to_sheet
    now = now or _utcnow()
    row.sheets_sync_attempts += 1
    try:
        sender(build_payload(row))
    except SheetsSyncError as exc:
        code = exc.code
    except Exception as exc:  # type only: a message could echo request data
        code = f"error_{type(exc).__name__}"[:100]
    else:
        row.sheets_sync_status = SYNCED
        row.sheets_synced_at = now
        row.sheets_next_attempt_at = None
        row.sheets_last_error = None
        db.commit()
        return SYNCED

    row.sheets_last_error = code
    if row.sheets_sync_attempts >= settings.feedback_sheets_max_attempts:
        row.sheets_sync_status = FAILED
        row.sheets_next_attempt_at = None
    else:
        row.sheets_sync_status = PENDING
        row.sheets_next_attempt_at = now + timedelta(seconds=backoff_seconds(row.sheets_sync_attempts))
    db.commit()
    logger.warning(
        "feedback sheets sync %s submission_id=%s attempt=%d error=%s",
        "gave up" if row.sheets_sync_status == FAILED else "will retry",
        row.submission_id, row.sheets_sync_attempts, code,
    )
    return row.sheets_sync_status


def request_stop() -> None:
    _stop.set()


def _claim(db: Session, row_id: int, now: datetime, ignore_schedule: bool = False) -> InaugurationFeedback | None:
    """Lock one PENDING row for this worker, or return None if another worker holds it or it is
    no longer eligible. The status and schedule are checked again under the lock: another worker
    may have synced or rescheduled the row between listing it and locking it.
    The lock lasts until sync_row commits, so concurrent workers/instances never send the same row."""
    stmt = select(InaugurationFeedback).where(
        InaugurationFeedback.id == row_id, InaugurationFeedback.sheets_sync_status == PENDING
    )
    if ignore_schedule:
        # first sync only: a row that already has an attempt belongs to the retry schedule
        stmt = stmt.where(InaugurationFeedback.sheets_sync_attempts == 0)
    else:
        stmt = stmt.where(or_(InaugurationFeedback.sheets_next_attempt_at.is_(None),
                              InaugurationFeedback.sheets_next_attempt_at <= now))
    return db.scalars(
        stmt.with_for_update(skip_locked=True).execution_options(populate_existing=True)
    ).first()


def sync_row_if_unclaimed(db: Session, row_id: int, sender: Callable[[dict], str] | None = None,
                          now: datetime | None = None) -> str | None:
    """First sync of a just-saved row (its retry time is deliberately in the future). Skipped if a
    worker already holds the row or it is no longer PENDING. Returns the new status, or None if skipped."""
    row = _claim(db, row_id, now or _utcnow(), ignore_schedule=True)
    if row is None:
        db.rollback()
        return None
    return sync_row(db, row, sender=sender, now=now)


def run_due_syncs(db: Session, limit: int = 20,
                  sender: Callable[[dict], str] | None = None, now: datetime | None = None) -> int:
    """Retry PENDING rows whose retry time has come. Returns how many were attempted."""
    now = now or _utcnow()
    deadline = time.monotonic() + _PASS_BUDGET_SECONDS
    due = db.scalars(
        select(InaugurationFeedback.id)
        .where(
            InaugurationFeedback.sheets_sync_status == PENDING,
            or_(InaugurationFeedback.sheets_next_attempt_at.is_(None),
                InaugurationFeedback.sheets_next_attempt_at <= now),
        )
        .order_by(InaugurationFeedback.id)
        .limit(limit)
    ).all()
    db.rollback()  # end the read transaction; each row is then handled in its own short transaction
    attempted = 0
    for row_id in due:
        if _stop.is_set() or time.monotonic() > deadline:
            break
        row = _claim(db, row_id, now)
        if row is None:
            db.rollback()
            continue
        sync_row(db, row, sender=sender, now=now)
        attempted += 1
    return attempted


def reset_failed(db: Session, include_synced: bool = False) -> int:
    """Put FAILED rows back in the queue (after fixing the configuration). With include_synced,
    SYNCED rows are re-queued too, to rebuild a sheet that was cleared or lost; the Apps Script
    skips any Submission ID it already holds."""
    wanted = (FAILED, SYNCED) if include_synced else (FAILED,)
    rows = db.scalars(select(InaugurationFeedback).where(InaugurationFeedback.sheets_sync_status.in_(wanted))).all()
    for row in rows:
        row.sheets_sync_status = PENDING
        row.sheets_sync_attempts = 0
        row.sheets_next_attempt_at = None
        row.sheets_synced_at = None
    db.commit()
    return len(rows)


def sync_new_submission(feedback_id: int) -> None:
    """Background task run right after a submission is committed. Swallows every error:
    the submitter already has their success response and the worker will retry."""
    if not settings.feedback_sheets_configured:
        return
    try:
        with SessionLocal() as db:
            sync_row_if_unclaimed(db, feedback_id)
    except Exception as exc:
        logger.error("feedback sheets first sync crashed error_type=%s", type(exc).__name__)


def run_due_syncs_once() -> int:
    with SessionLocal() as db:
        return run_due_syncs(db)


async def run_feedback_sheets_worker() -> None:
    """Started once at app startup. Does nothing until the mirror is configured, then
    keeps retrying PENDING rows. State lives in the database, so restarts lose nothing."""
    _stop.clear()
    while not _stop.is_set():
        try:
            if settings.feedback_sheets_configured:
                attempted = await asyncio.to_thread(run_due_syncs_once)
                if attempted:
                    logger.info("feedback sheets retry pass attempted=%d", attempted)
        except Exception as exc:
            logger.error("feedback sheets worker pass failed error_type=%s", type(exc).__name__)
        await asyncio.sleep(settings.feedback_sheets_retry_interval_seconds)
