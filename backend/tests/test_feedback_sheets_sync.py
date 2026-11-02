"""Inauguration feedback -> Google Sheet mirror: persistence first, retry, de-duplication, privacy.

Runs against an isolated SQLite database and a fake sender / mocked HTTP transport, so no
real network, spreadsheet or PostgreSQL is touched.
"""
import re
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.core import feedback_sheets as fs
from app.core.rate_limit import limiter
from app.database import get_db
from app.main import app
from app.models.inauguration_feedback import InaugurationFeedback

URL = "/api/feedback/inauguration"
WEBAPP = "https://script.google.com/macros/s/FAKE-DEPLOYMENT-ID/exec"
SECRET = "test-shared-secret-not-real"
PHONE = "9876543210"
GS_FILE = Path(__file__).resolve().parent.parent / "integrations" / "google_sheets" / "FeedbackSheet.gs"


def valid(**over):
    body = {
        "name": "Asha Verma",
        "domain": "Cloud",
        "overall_session": "Excellent",
        "usefulness": "Very Useful",
        "learned_something": "Yes",
        "liked_topic": "AI",
        "attend_future": "Yes",
        "contact_preference": "no",
    }
    body.update(over)
    return body


class Recorder:
    """Fake Apps Script sender: records payloads, can be told to fail."""

    def __init__(self, outcome="appended"):
        self.outcome = outcome
        self.payloads = []

    def __call__(self, payload):
        self.payloads.append(payload)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


class SheetsTestBase(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        InaugurationFeedback.__table__.create(self.engine)
        self.factory = sessionmaker(bind=self.engine)

        def override():
            with Session(self.engine) as db:
                yield db

        app.dependency_overrides[get_db] = override
        limiter.enabled = False
        self.client = TestClient(app)
        self.patches = [
            mock.patch.object(fs, "SessionLocal", self.factory),
            mock.patch.object(settings, "feedback_sheets_webapp_url", WEBAPP),
            mock.patch.object(settings, "feedback_sheets_shared_secret", SECRET),
            mock.patch.object(settings, "feedback_sheets_max_attempts", 4),
            mock.patch.object(settings, "feedback_sheets_retry_interval_seconds", 60),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        app.dependency_overrides.pop(get_db, None)
        limiter.enabled = True
        self.engine.dispose()

    def rows(self):
        with Session(self.engine) as db:
            return db.scalars(select(InaugurationFeedback).order_by(InaugurationFeedback.id)).all()

    def submit(self, sender, **over):
        with mock.patch.object(fs, "post_to_sheet", sender):
            return self.client.post(URL, json=valid(**over))


class SubmissionFlowTests(SheetsTestBase):
    def test_normal_submission_is_saved_then_synced(self):
        sender = Recorder()
        res = self.submit(sender, suggestion="More labs")
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.json()["detail"], "Thank you for your valuable feedback!")
        row = self.rows()[0]
        self.assertEqual(row.sheets_sync_status, fs.SYNCED)
        self.assertEqual(row.sheets_sync_attempts, 1)
        self.assertIsNotNone(row.sheets_synced_at)
        self.assertIsNone(row.sheets_last_error)
        self.assertEqual(len(sender.payloads), 1)
        sent = sender.payloads[0]
        self.assertEqual(sent["submission_id"], row.submission_id)
        self.assertRegex(sent["submission_id"], r"^[0-9a-f-]{36}$")
        self.assertEqual((sent["name"], sent["domain"], sent["suggestion"]), ("Asha Verma", "Cloud", "More labs"))
        self.assertRegex(sent["submitted_at"], r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d UTC$")

    def test_optional_fields_empty(self):
        sender = Recorder()
        self.assertEqual(self.submit(sender).status_code, 201)
        sent = sender.payloads[0]
        for key in ("domain_other", "liked_topic_other", "suggestion", "contact_number"):
            self.assertEqual(sent[key], "", key)
        self.assertEqual(sent["contact_preference"], "No")

    def test_course_contact_consent_sends_phone(self):
        sender = Recorder()
        res = self.submit(sender, contact_preference="yes", contact_number="+91 98765-43210")
        self.assertEqual(res.status_code, 201)
        sent = sender.payloads[0]
        self.assertEqual((sent["contact_preference"], sent["contact_number"]), ("Yes", PHONE))
        sender2 = Recorder()
        self.submit(sender2, contact_preference="maybe", contact_number=PHONE)
        self.assertEqual(sender2.payloads[0]["contact_preference"], "Maybe")
        self.assertEqual(sender2.payloads[0]["contact_number"], PHONE)

    def test_phone_never_sent_without_consent(self):
        sender = Recorder()
        self.submit(sender, contact_preference="no", contact_number=PHONE)
        self.assertEqual(sender.payloads[0]["contact_number"], "")
        self.assertNotIn(PHONE, str(sender.payloads[0]))
        # Even if a row somehow carried a number, an opted-out row never exports it.
        row = InaugurationFeedback(**valid(contact_number=PHONE), submission_id="x")
        self.assertEqual(fs.build_payload(row)["contact_number"], "")

    def test_payload_has_no_secret_or_spreadsheet_id(self):
        sender = Recorder()
        self.submit(sender)
        text = str(sender.payloads[0]).lower()
        self.assertNotIn("secret", text)
        self.assertNotIn("spreadsheet", text)
        self.assertNotIn("1lnlppvmjx", text)

    def test_sheets_unavailable_still_saves_and_confirms(self):
        sender = Recorder(fs.SheetsSyncError("timeout"))
        res = self.submit(sender, contact_preference="yes", contact_number=PHONE)
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.json()["detail"], "Thank you for your valuable feedback!")
        rows = self.rows()
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row.name, "Asha Verma")
        self.assertEqual(row.contact_number, PHONE)
        self.assertEqual(row.sheets_sync_status, fs.PENDING)
        self.assertEqual(row.sheets_sync_attempts, 1)
        self.assertEqual(row.sheets_last_error, "timeout")
        self.assertIsNotNone(row.sheets_next_attempt_at)
        self.assertIsNone(row.sheets_synced_at)

    def test_unexpected_sender_crash_is_not_shown_and_not_marked_synced(self):
        sender = Recorder(RuntimeError(f"boom for Asha Verma {PHONE}"))
        res = self.submit(sender)
        self.assertEqual(res.status_code, 201)
        row = self.rows()[0]
        self.assertEqual(row.sheets_sync_status, fs.PENDING)
        self.assertEqual(row.sheets_last_error, "error_RuntimeError")
        self.assertNotIn(PHONE, row.sheets_last_error)

    def test_not_configured_saves_and_stays_pending_without_attempts(self):
        with mock.patch.object(settings, "feedback_sheets_webapp_url", ""):
            sender = Recorder()
            res = self.submit(sender)
        self.assertEqual(res.status_code, 201)
        self.assertEqual(sender.payloads, [])
        row = self.rows()[0]
        self.assertEqual((row.sheets_sync_status, row.sheets_sync_attempts), (fs.PENDING, 0))

    def test_new_row_is_not_picked_up_by_the_worker_immediately(self):
        sender = Recorder(fs.SheetsSyncError("http_500"))
        self.submit(sender)
        late = Recorder()
        with self.factory() as db:
            attempted = fs.run_due_syncs(db, sender=late)  # before the retry time
        self.assertEqual(attempted, 0)


class RetryTests(SheetsTestBase):
    def failed_row(self, sender=None):
        self.submit(sender or Recorder(fs.SheetsSyncError("http_503")))
        return self.rows()[0]

    def test_retry_after_failed_sync_succeeds_and_clears_error(self):
        row = self.failed_row()
        due = row.sheets_next_attempt_at.replace(tzinfo=timezone.utc) + timedelta(seconds=1)
        ok = Recorder("appended")
        with self.factory() as db:
            self.assertEqual(fs.run_due_syncs(db, sender=ok, now=due), 1)
        row = self.rows()[0]
        self.assertEqual(row.sheets_sync_status, fs.SYNCED)
        self.assertEqual(row.sheets_sync_attempts, 2)
        self.assertIsNone(row.sheets_last_error)
        self.assertIsNone(row.sheets_next_attempt_at)
        # The same Submission ID is sent on every attempt (this is what de-duplicates the sheet).
        self.assertEqual(ok.payloads[0]["submission_id"], row.submission_id)
        # A synced row is never sent again.
        with self.factory() as db:
            self.assertEqual(fs.run_due_syncs(db, sender=ok, now=due + timedelta(days=1)), 0)
        self.assertEqual(len(ok.payloads), 1)

    def test_not_retried_before_its_time_and_backoff_grows(self):
        row = self.failed_row()
        first_due = row.sheets_next_attempt_at.replace(tzinfo=timezone.utc)
        sender = Recorder(fs.SheetsSyncError("http_503"))
        with self.factory() as db:
            self.assertEqual(fs.run_due_syncs(db, sender=sender, now=first_due - timedelta(seconds=5)), 0)
            self.assertEqual(fs.run_due_syncs(db, sender=sender, now=first_due), 1)
        second_due = self.rows()[0].sheets_next_attempt_at.replace(tzinfo=timezone.utc)
        self.assertEqual((second_due - first_due).total_seconds(), 120)  # 60s, then 120s
        self.assertEqual(fs.backoff_seconds(1), 60)
        self.assertEqual(fs.backoff_seconds(3), 240)
        self.assertEqual(fs.backoff_seconds(30), 3600)

    def test_exhausted_attempts_become_failed_never_synced_then_can_be_requeued(self):
        row = self.failed_row()  # attempt 1 of 4
        now = row.sheets_next_attempt_at.replace(tzinfo=timezone.utc)
        bad = Recorder(fs.SheetsSyncError("script_unauthorized"))
        with self.factory() as db:
            for _ in range(5):
                now += timedelta(hours=2)
                fs.run_due_syncs(db, sender=bad, now=now)
        row = self.rows()[0]
        self.assertEqual(row.sheets_sync_status, fs.FAILED)
        self.assertEqual(row.sheets_sync_attempts, 4)
        self.assertEqual(row.sheets_last_error, "script_unauthorized")
        self.assertIsNone(row.sheets_synced_at)
        self.assertEqual(len(bad.payloads), 3)  # nothing after FAILED
        with self.factory() as db:
            self.assertEqual(fs.reset_failed(db), 1)
        row = self.rows()[0]
        self.assertEqual((row.sheets_sync_status, row.sheets_sync_attempts), (fs.PENDING, 0))
        good = Recorder()
        with self.factory() as db:
            self.assertEqual(fs.run_due_syncs(db, sender=good, now=now + timedelta(days=1)), 1)
        self.assertEqual(self.rows()[0].sheets_sync_status, fs.SYNCED)

    def test_stale_claim_cannot_retry_a_row_another_worker_just_rescheduled(self):
        """A worker that listed the row as due must not send it if, by the time it locks it,
        another worker has failed and rescheduled it (or synced it)."""
        row = self.failed_row()
        listed_at = row.sheets_next_attempt_at.replace(tzinfo=timezone.utc) + timedelta(seconds=1)
        with self.factory() as worker_a, self.factory() as worker_b:
            sender = Recorder(fs.SheetsSyncError("http_503"))
            fs.run_due_syncs(worker_a, sender=sender, now=listed_at)  # A fails -> next attempt is in the future
            self.assertIsNone(fs._claim(worker_b, row.id, now=listed_at))
            # the immediate first-sync path is only for rows with no attempt yet
            self.assertIsNone(fs.sync_row_if_unclaimed(worker_b, row.id, sender=sender, now=listed_at))
        self.assertEqual(len(sender.payloads), 1)

    def test_synced_row_is_never_claimed(self):
        row = self.failed_row()
        with self.factory() as db:
            fs.sync_row(db, db.get(InaugurationFeedback, row.id), sender=Recorder())
            self.assertIsNone(fs._claim(db, row.id, now=datetime.now(timezone.utc), ignore_schedule=True))
            late = Recorder()
            self.assertIsNone(fs.sync_row_if_unclaimed(db, row.id, sender=late))
        self.assertEqual(late.payloads, [])

    def test_stop_flag_halts_a_retry_pass(self):
        self.submit(Recorder(fs.SheetsSyncError("http_503")))
        self.submit(Recorder(fs.SheetsSyncError("http_503")))
        later = datetime.now(timezone.utc) + timedelta(hours=1)
        sender = Recorder()
        fs.request_stop()
        try:
            with self.factory() as db:
                self.assertEqual(fs.run_due_syncs(db, sender=sender, now=later), 0)
        finally:
            fs._stop.clear()
        self.assertEqual(sender.payloads, [])
        with self.factory() as db:
            self.assertEqual(fs.run_due_syncs(db, sender=sender, now=later), 2)

    def test_resync_all_requeues_synced_rows_for_a_lost_sheet(self):
        self.submit(Recorder())
        self.assertEqual(self.rows()[0].sheets_sync_status, fs.SYNCED)
        with self.factory() as db:
            self.assertEqual(fs.reset_failed(db), 0)
            self.assertEqual(fs.reset_failed(db, include_synced=True), 1)
        row = self.rows()[0]
        self.assertEqual((row.sheets_sync_status, row.sheets_sync_attempts), (fs.PENDING, 0))
        self.assertIsNone(row.sheets_synced_at)

    def test_duplicate_reply_counts_as_synced(self):
        row = self.failed_row()
        due = row.sheets_next_attempt_at.replace(tzinfo=timezone.utc)
        with self.factory() as db:
            fs.run_due_syncs(db, sender=Recorder("duplicate"), now=due)
        self.assertEqual(self.rows()[0].sheets_sync_status, fs.SYNCED)

    def test_submission_id_is_unique_in_the_database(self):
        self.submit(Recorder())
        existing = self.rows()[0].submission_id
        with Session(self.engine) as db:
            db.add(InaugurationFeedback(**valid(), submission_id=existing))
            with self.assertRaises(IntegrityError):
                db.commit()
        self.submit(Recorder())
        ids = [r.submission_id for r in self.rows()]
        self.assertEqual(len(set(ids)), 2)


class PrivacyTests(SheetsTestBase):
    def test_logs_contain_no_personal_data(self):
        sender = Recorder(RuntimeError(f"leak {PHONE} Asha Verma"))
        with self.assertLogs("feedback_sheets", level="WARNING") as logs:
            self.submit(sender, contact_preference="yes", contact_number=PHONE, suggestion="my secret idea")
        text = "\n".join(logs.output)
        self.assertIn("submission_id=", text)
        for forbidden in (PHONE, "Asha", "my secret idea", SECRET, "FAKE-DEPLOYMENT-ID"):
            self.assertNotIn(forbidden, text)

    def test_formula_injection_is_neutralised(self):
        sender = Recorder()
        self.submit(
            sender, name="=HYPERLINK(\"http://evil\")", domain="Others", domain_other="@SUM(1)",
            suggestion="-2+3", liked_topic="Other", liked_topic_other="+cmd",
        )
        sent = sender.payloads[0]
        self.assertEqual(sent["name"], "'=HYPERLINK(\"http://evil\")")
        self.assertEqual(sent["domain_other"], "'@SUM(1)")
        self.assertEqual(sent["suggestion"], "'-2+3")
        self.assertEqual(sent["liked_topic_other"], "'+cmd")
        self.assertEqual(fs.sanitize_cell("\t=1"), "'\t=1")
        self.assertEqual(fs.sanitize_cell("Plain text"), "Plain text")
        self.assertEqual(fs.sanitize_cell("a=b"), "a=b")
        self.assertEqual(fs.sanitize_cell(None), "")
        # The stored database value stays the user's original text.
        self.assertEqual(self.rows()[0].name, "=HYPERLINK(\"http://evil\")")


class TransportTests(unittest.TestCase):
    """post_to_sheet against a mocked HTTP layer."""

    def setUp(self):
        self.calls = []
        for name, value in (("feedback_sheets_webapp_url", WEBAPP), ("feedback_sheets_shared_secret", SECRET)):
            p = mock.patch.object(settings, name, value)
            p.start()
            self.addCleanup(p.stop)

    def run_with(self, handler):
        real = httpx.Client

        def make(*args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            return real(*args, **kwargs)

        with mock.patch.object(fs.httpx, "Client", make):
            return fs.post_to_sheet({"submission_id": "abc", "name": "Asha"})

    def error_code(self, handler):
        with self.assertRaises(fs.SheetsSyncError) as ctx:
            self.run_with(handler)
        return ctx.exception.code

    def test_success_sends_secret_in_body_to_configured_url_only(self):
        def handler(request):
            self.calls.append(request)
            return httpx.Response(200, json={"ok": True, "result": "appended"})

        self.assertEqual(self.run_with(handler), "appended")
        req = self.calls[0]
        self.assertEqual(str(req.url), WEBAPP)
        body = req.read().decode()
        self.assertIn(SECRET, body)
        self.assertNotIn("spreadsheet", body.lower())

    def test_duplicate_reply(self):
        self.assertEqual(self.run_with(lambda r: httpx.Response(200, json={"ok": True, "result": "duplicate"})), "duplicate")

    def test_failures_become_short_codes(self):
        self.assertEqual(self.error_code(lambda r: httpx.Response(500, text="oops")), "http_500")
        self.assertEqual(self.error_code(lambda r: httpx.Response(200, text="<html>login</html>")), "bad_response")
        self.assertEqual(self.error_code(lambda r: httpx.Response(200, json={"ok": False, "error": "unauthorized"})), "script_unauthorized")
        self.assertEqual(self.error_code(lambda r: httpx.Response(200, json={"ok": False, "error": "Bad; Value 9876543210"})), "script_unknown")
        self.assertEqual(self.error_code(lambda r: httpx.Response(200, json={"ok": True, "result": "weird"})), "script_unknown")

        def timeout(request):
            raise httpx.ReadTimeout("slow", request=request)

        def down(request):
            raise httpx.ConnectError(f"cannot reach {WEBAPP}", request=request)

        self.assertEqual(self.error_code(timeout), "timeout")
        code = self.error_code(down)
        self.assertEqual(code, "network_error")
        self.assertNotIn("FAKE-DEPLOYMENT-ID", code)


class ContractTests(unittest.TestCase):
    def test_python_columns_match_the_apps_script_headers(self):
        source = GS_FILE.read_text(encoding="utf-8")
        block = re.search(r"var HEADERS = \[(.*?)\];", source, re.S).group(1)
        script_headers = re.findall(r"'([^']+)'", block)
        self.assertEqual(script_headers, fs.SHEET_COLUMNS)
        self.assertEqual(len(fs.SHEET_COLUMNS), 15)

    def test_no_secret_or_spreadsheet_id_in_frontend_or_backend_defaults(self):
        root = Path(__file__).resolve().parent.parent.parent / "arckenites-orange"
        for path in (root / "js" / "inauguration-feedback.js", root / "inauguration-feedback.html"):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("docs.google.com", text)
            self.assertNotIn("script.google.com", text)
            self.assertNotIn("1lNLPpvMJx", text)
        self.assertEqual(type(settings).model_fields["feedback_sheets_webapp_url"].default, "")
        self.assertEqual(type(settings).model_fields["feedback_sheets_shared_secret"].default, "")


if __name__ == "__main__":
    unittest.main()
