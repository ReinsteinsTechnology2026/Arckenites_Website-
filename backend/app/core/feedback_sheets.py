"""Write one inauguration feedback submission to the Google Sheet.

The sheet is the ONLY store for this feedback: nothing is saved in PostgreSQL and there is no
queue or retry worker. The request handler calls the Google Apps Script Web App
(backend/integrations/google_sheets/FeedbackSheet.gs) and reports success only after the script
confirms the row is in the sheet. If Google is unavailable the visitor gets an error and
retries; the retry reuses the same submission id, which the script uses to avoid duplicate rows.

The Apps Script URL and shared secret come from the server environment only. The spreadsheet id
is configured inside the Apps Script, so no request can name a different spreadsheet.
"""
import logging
import re
from datetime import datetime, timezone

import httpx

from app.config import settings
from app.schemas.inauguration_feedback import InaugurationFeedbackRequest

logger = logging.getLogger("feedback_sheets")
# httpx logs every request URL at INFO. The Apps Script URL is confidential, so keep it out of any INFO log.
logging.getLogger("httpx").setLevel(logging.WARNING)

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
_CONTACT_LABELS = {"yes": "Yes", "maybe": "Maybe", "no": "No"}


class SheetsError(Exception):
    """Writing to the sheet failed. `code` is a short, data-free label safe to log."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def sanitize_cell(value: str | None) -> str:
    """Neutralise spreadsheet formula injection: a leading = + - @ (or tab/CR) gets a
    single-quote prefix so the cell is read as text, including after CSV export."""
    text = "" if value is None else str(value)
    return "'" + text if text.startswith(_FORMULA_PREFIXES) else text


def build_payload(feedback: InaugurationFeedbackRequest, submitted_at: datetime | None = None) -> dict:
    """The data sent to the Apps Script (without the secret). The contact number is sent
    only when the visitor chose "Yes" or "Maybe" for course contact."""
    submitted_at = submitted_at or datetime.now(timezone.utc)
    opted_in = feedback.contact_preference in ("yes", "maybe")
    return {
        "submission_id": feedback.submission_id,
        "submitted_at": submitted_at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "name": sanitize_cell(feedback.name),
        "domain": sanitize_cell(feedback.domain),
        "domain_other": sanitize_cell(feedback.domain_other),
        "overall_session": sanitize_cell(feedback.overall_session),
        "usefulness": sanitize_cell(feedback.usefulness),
        "learned_something": sanitize_cell(feedback.learned_something),
        "liked_topic": sanitize_cell(feedback.liked_topic),
        "liked_topic_other": sanitize_cell(feedback.liked_topic_other),
        "attend_future": sanitize_cell(feedback.attend_future),
        "suggestion": sanitize_cell(feedback.suggestion),
        "contact_preference": _CONTACT_LABELS.get(feedback.contact_preference, ""),
        "contact_number": sanitize_cell(feedback.contact_number) if opted_in else "",
    }


def post_to_sheet(payload: dict) -> str:
    """POST to the Apps Script Web App. Returns "appended" or "duplicate" (the row was already
    in the sheet, e.g. an earlier attempt succeeded but its reply was lost). Raises SheetsError
    otherwise. Errors never include the URL, secret or any form data."""
    body = {**payload, "secret": settings.feedback_sheets_shared_secret}
    try:
        # Apps Script answers a POST with a redirect to the result page; follow it.
        with httpx.Client(timeout=httpx.Timeout(settings.feedback_sheets_timeout_seconds, connect=5.0),
                          follow_redirects=True) as client:
            response = client.post(settings.feedback_sheets_webapp_url, json=body)
    except httpx.TimeoutException:
        raise SheetsError("timeout") from None
    except httpx.HTTPError:
        raise SheetsError("network_error") from None
    if response.status_code != 200:
        raise SheetsError(f"http_{response.status_code}")
    try:
        data = response.json()
    except ValueError:
        raise SheetsError("bad_response") from None
    if isinstance(data, dict) and data.get("ok") is True and data.get("result") in ("appended", "duplicate"):
        return data["result"]
    error = data.get("error") if isinstance(data, dict) else None
    code = error if isinstance(error, str) and re.fullmatch(r"[a-z_]{1,40}", error) else "unknown"
    raise SheetsError(f"script_{code}")
