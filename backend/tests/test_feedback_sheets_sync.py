"""Google Sheets as the only feedback store: HTTP transport, payload contract, and a guard that the
PostgreSQL feedback storage / retry machinery stays removed. No real network is used."""
import re
import unittest
from pathlib import Path
from unittest import mock

import httpx

from app.config import settings
from app.core import feedback_sheets as fs
from app.schemas.inauguration_feedback import InaugurationFeedbackRequest

WEBAPP = "https://script.google.com/macros/s/FAKE-DEPLOYMENT-ID/exec"
SECRET = "test-shared-secret-not-real"
BACKEND = Path(__file__).resolve().parent.parent
GS_FILE = BACKEND / "integrations" / "google_sheets" / "FeedbackSheet.gs"


def request(**over):
    body = {
        "submission_id": "123e4567-e89b-42d3-a456-426614174000", "name": "Asha", "domain": "Cloud",
        "overall_session": "Excellent", "usefulness": "Very Useful", "learned_something": "Yes",
        "liked_topic": "AI", "attend_future": "Yes", "contact_preference": "no",
    }
    body.update(over)
    return InaugurationFeedbackRequest(**body)


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
        with self.assertRaises(fs.SheetsError) as ctx:
            self.run_with(handler)
        return ctx.exception.code

    def test_success_sends_secret_in_body_to_configured_url_only(self):
        def handler(req):
            self.calls.append(req)
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


class PayloadTests(unittest.TestCase):
    def test_payload_has_no_secret_or_spreadsheet_id(self):
        text = str(fs.build_payload(request())).lower()
        self.assertNotIn("secret", text)
        self.assertNotIn("spreadsheet", text)
        self.assertNotIn("1lnlppvmjx", text)

    def test_phone_only_with_consent(self):
        self.assertEqual(fs.build_payload(request(contact_preference="yes", contact_number="98765 43210"))["contact_number"], "9876543210")
        self.assertEqual(fs.build_payload(request(contact_preference="no", contact_number="9876543210"))["contact_number"], "")


class ContractTests(unittest.TestCase):
    def test_python_columns_match_the_apps_script_headers(self):
        source = GS_FILE.read_text(encoding="utf-8")
        block = re.search(r"var HEADERS = \[(.*?)\];", source, re.S).group(1)
        self.assertEqual(re.findall(r"'([^']+)'", block), fs.SHEET_COLUMNS)
        self.assertEqual(len(fs.SHEET_COLUMNS), 15)

    def test_no_secret_or_sheet_reference_in_the_frontend(self):
        site = BACKEND.parent / "arckenites-orange"
        for path in (site / "js" / "inauguration-feedback.js", site / "inauguration-feedback.html"):
            text = path.read_text(encoding="utf-8")
            for forbidden in ("docs.google.com", "script.google.com", "1lNLPpvMJx", "SHARED_SECRET", "shared_secret"):
                self.assertNotIn(forbidden, text, f"{forbidden} in {path.name}")

    def test_secret_and_url_default_to_blank(self):
        cls = type(settings)
        self.assertEqual(cls.model_fields["feedback_sheets_webapp_url"].default, "")
        self.assertEqual(cls.model_fields["feedback_sheets_shared_secret"].default, "")


class PostgresFeedbackStorageIsRemovedTests(unittest.TestCase):
    """Guards for the requirement that the sheet is the ONLY store (no DB model, migration or worker)."""

    def test_no_feedback_model_or_migration(self):
        self.assertFalse((BACKEND / "app" / "models" / "inauguration_feedback.py").exists())
        import app.models as models
        self.assertFalse(hasattr(models, "InaugurationFeedback"))
        for path in (BACKEND / "alembic" / "versions").glob("*.py"):
            self.assertNotIn("inauguration_feedback", path.read_text(encoding="utf-8-sig"), path.name)

    def test_no_retry_worker_hooks_or_settings(self):
        main = (BACKEND / "app" / "main.py").read_text(encoding="utf-8")
        self.assertNotIn("feedback_sheets_worker", main)
        for name in ("feedback_sheets_max_attempts", "feedback_sheets_retry_interval_seconds"):
            self.assertNotIn(name, type(settings).model_fields)
        for name in ("run_due_syncs", "run_feedback_sheets_worker", "sync_row", "request_stop"):
            self.assertFalse(hasattr(fs, name), name)
        self.assertFalse((BACKEND / "maintenance" / "retry_feedback_sheet_sync.py").exists())

    def test_env_example_has_only_url_secret_timeout(self):
        text = (BACKEND / ".env.example").read_text(encoding="utf-8")
        self.assertIn("FEEDBACK_SHEETS_WEBAPP_URL=", text)
        self.assertIn("FEEDBACK_SHEETS_SHARED_SECRET=", text)
        for gone in ("MAX_ATTEMPTS", "RETRY_INTERVAL"):
            self.assertNotIn(gone, text)


if __name__ == "__main__":
    unittest.main()
