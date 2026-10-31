"""Inauguration feedback endpoint: validation, and the Google Sheet as the ONLY store.

The sheet writer is replaced by a fake, so no network, spreadsheet or database is involved.
"""
import unittest
import uuid
from unittest import mock

from fastapi.testclient import TestClient

from app.api import routes_inauguration_feedback as route
from app.config import settings
from app.core import feedback_sheets as fs
from app.core.rate_limit import limiter
from app.database import get_db
from app.main import app
from app.schemas.inauguration_feedback import normalize_indian_mobile

URL = "/api/feedback/inauguration"
SECRET = "test-shared-secret-not-real"
PHONE = "9876543210"
THANKS = "Thank you for your valuable feedback!"


def valid(**over):
    body = {
        "submission_id": str(uuid.uuid4()),
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


class FakeSheet:
    """Stands in for post_to_sheet: records every payload, can be told to fail."""

    def __init__(self, outcome="appended"):
        self.outcome = outcome
        self.payloads = []

    def __call__(self, payload):
        self.payloads.append(payload)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


class FeedbackEndpointTests(unittest.TestCase):
    def setUp(self):
        limiter.enabled = False
        self.client = TestClient(app)
        self.patches = [
            mock.patch.object(settings, "feedback_sheets_webapp_url", "https://script.google.com/macros/s/FAKE-DEPLOYMENT-ID/exec"),
            mock.patch.object(settings, "feedback_sheets_shared_secret", SECRET),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        app.dependency_overrides.pop(get_db, None)
        limiter.enabled = True

    def submit(self, sheet, **over):
        with mock.patch.object(route, "post_to_sheet", sheet):
            return self.client.post(URL, json=valid(**over))

    # --- success -----------------------------------------------------------------------

    def test_valid_submission_goes_to_the_sheet_and_confirms(self):
        sheet = FakeSheet()
        res = self.submit(sheet, suggestion="  More labs  ")
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.json()["detail"], THANKS)
        self.assertEqual(len(sheet.payloads), 1)
        sent = sheet.payloads[0]
        self.assertEqual((sent["name"], sent["domain"], sent["suggestion"]), ("Asha Verma", "Cloud", "More labs"))
        self.assertRegex(sent["submitted_at"], r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d UTC$")

    def test_empty_optional_fields(self):
        sheet = FakeSheet()
        self.assertEqual(self.submit(sheet).status_code, 201)
        sent = sheet.payloads[0]
        for key in ("domain_other", "liked_topic_other", "suggestion", "contact_number"):
            self.assertEqual(sent[key], "", key)
        self.assertEqual(sent["contact_preference"], "No")

    def test_duplicate_reply_from_the_sheet_is_success(self):
        res = self.submit(FakeSheet("duplicate"))
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.json()["detail"], THANKS)

    def test_endpoint_does_not_use_the_database(self):
        def boom():
            raise AssertionError("the feedback endpoint must not open a database session")
            yield  # pragma: no cover

        app.dependency_overrides[get_db] = boom
        self.assertEqual(self.submit(FakeSheet()).status_code, 201)
        for r in app.routes:
            if getattr(r, "path", "") == URL:
                self.assertEqual(r.dependant.dependencies, [], "no dependencies (no db session) on the route")

    # --- consent -----------------------------------------------------------------------

    def test_course_contact_consent_sends_phone(self):
        sheet = FakeSheet()
        self.assertEqual(self.submit(sheet, contact_preference="yes", contact_number="+91 98765-43210").status_code, 201)
        self.assertEqual((sheet.payloads[0]["contact_preference"], sheet.payloads[0]["contact_number"]), ("Yes", PHONE))
        sheet2 = FakeSheet()
        self.submit(sheet2, contact_preference="maybe", contact_number=PHONE)
        self.assertEqual((sheet2.payloads[0]["contact_preference"], sheet2.payloads[0]["contact_number"]), ("Maybe", PHONE))

    def test_phone_never_sent_without_consent(self):
        sheet = FakeSheet()
        self.submit(sheet, contact_preference="no", contact_number=PHONE)
        self.assertEqual(sheet.payloads[0]["contact_number"], "")
        self.assertNotIn(PHONE, str(sheet.payloads[0]))

    def test_phone_required_and_validated_for_yes_and_maybe(self):
        for pref in ("yes", "maybe"):
            sheet = FakeSheet()
            self.assertEqual(self.submit(sheet, contact_preference=pref).status_code, 422)
            self.assertEqual(self.submit(sheet, contact_preference=pref, contact_number="12345").status_code, 422)
            self.assertEqual(sheet.payloads, [], "nothing is written for an invalid request")

    # --- formula injection -------------------------------------------------------------

    def test_formula_injection_is_neutralised_before_the_sheet(self):
        sheet = FakeSheet()
        self.submit(sheet, name='=HYPERLINK("http://evil")', domain="Others", domain_other="@SUM(1)",
                    suggestion="-2+3", liked_topic="Other", liked_topic_other="+cmd")
        sent = sheet.payloads[0]
        self.assertEqual(sent["name"], "'=HYPERLINK(\"http://evil\")")
        self.assertEqual(sent["domain_other"], "'@SUM(1)")
        self.assertEqual(sent["suggestion"], "'-2+3")
        self.assertEqual(sent["liked_topic_other"], "'+cmd")
        self.assertEqual(fs.sanitize_cell("Plain text"), "Plain text")
        self.assertEqual(fs.sanitize_cell("\t=1"), "'\t=1")

    # --- Google Sheets unavailable: never report success -------------------------------

    def assert_visitor_told_to_retry(self, res):
        self.assertEqual(res.status_code, 503)
        self.assertEqual(res.json()["detail"], route.RETRY_MESSAGE)
        text = res.text
        for leaked in (SECRET, "FAKE-DEPLOYMENT-ID", "timeout", "unauthorized", "http_", "script_", THANKS):
            self.assertNotIn(leaked, text)

    def test_sheets_unavailable_returns_a_retryable_error_not_success(self):
        for code in ("timeout", "network_error", "http_500", "bad_response", "script_busy"):
            self.assert_visitor_told_to_retry(self.submit(FakeSheet(fs.SheetsError(code))))

    def test_invalid_shared_secret_is_a_server_error_not_success(self):
        self.assert_visitor_told_to_retry(self.submit(FakeSheet(fs.SheetsError("script_unauthorized"))))

    def test_unexpected_crash_is_a_retryable_error(self):
        self.assert_visitor_told_to_retry(self.submit(FakeSheet(RuntimeError(f"boom {PHONE} Asha"))))

    def test_not_configured_fails_closed(self):
        sheet = FakeSheet()
        with mock.patch.object(settings, "feedback_sheets_webapp_url", ""):
            self.assert_visitor_told_to_retry(self.submit(sheet))
        with mock.patch.object(settings, "feedback_sheets_shared_secret", ""):
            self.assert_visitor_told_to_retry(self.submit(sheet))
        self.assertEqual(sheet.payloads, [])

    def test_retry_after_failure_reuses_the_submission_id(self):
        body = valid(contact_preference="yes", contact_number=PHONE)
        down, up = FakeSheet(fs.SheetsError("timeout")), FakeSheet("appended")
        with mock.patch.object(route, "post_to_sheet", down):
            self.assertEqual(self.client.post(URL, json=body).status_code, 503)
        with mock.patch.object(route, "post_to_sheet", up):
            self.assertEqual(self.client.post(URL, json=body).status_code, 201)
        self.assertEqual(down.payloads[0]["submission_id"], up.payloads[0]["submission_id"])
        self.assertEqual(up.payloads[0]["submission_id"], body["submission_id"])

    def test_logs_contain_no_personal_data(self):
        with self.assertLogs("feedback_sheets", level="WARNING") as logs:
            self.submit(FakeSheet(fs.SheetsError("script_unauthorized")), contact_preference="yes",
                        contact_number=PHONE, suggestion="my private idea")
        text = "\n".join(logs.output)
        self.assertIn("script_unauthorized", text)
        for forbidden in (PHONE, "Asha", "my private idea", SECRET, "FAKE-DEPLOYMENT-ID", "script.google.com"):
            self.assertNotIn(forbidden, text)

    # --- validation (unchanged questions) ----------------------------------------------

    def test_submission_id_is_required_and_must_be_a_uuid(self):
        sheet = FakeSheet()
        body = valid()
        del body["submission_id"]
        with mock.patch.object(route, "post_to_sheet", sheet):
            self.assertEqual(self.client.post(URL, json=body).status_code, 422)
            for bad in ("", "abc", "x" * 36, "123e4567-e89b-42d3-a456-42661417400", str(uuid.uuid4()) + "0", "' OR 1=1 --"):
                self.assertEqual(self.client.post(URL, json=valid(submission_id=bad)).status_code, 422, bad)
        self.assertEqual(sheet.payloads, [])

    def test_submission_id_is_lowercased(self):
        sheet = FakeSheet()
        self.submit(sheet, submission_id="123E4567-E89B-42D3-A456-426614174000")
        self.assertEqual(sheet.payloads[0]["submission_id"], "123e4567-e89b-42d3-a456-426614174000")

    def test_missing_required_fields_rejected(self):
        sheet = FakeSheet()
        with mock.patch.object(route, "post_to_sheet", sheet):
            for field in ("name", "domain", "overall_session", "usefulness", "learned_something",
                          "liked_topic", "attend_future", "contact_preference"):
                body = valid()
                del body[field]
                self.assertEqual(self.client.post(URL, json=body).status_code, 422, field)
            self.assertEqual(self.client.post(URL, json=valid(name="   ")).status_code, 422)
        self.assertEqual(sheet.payloads, [])

    def test_invalid_choice_rejected(self):
        self.assertEqual(self.submit(FakeSheet(), overall_session="Terrible").status_code, 422)

    def test_others_domain_requires_text(self):
        self.assertEqual(self.submit(FakeSheet(), domain="Others").status_code, 422)
        sheet = FakeSheet()
        self.assertEqual(self.submit(sheet, domain="Others", domain_other="Data Science").status_code, 201)
        self.assertEqual(sheet.payloads[0]["domain_other"], "Data Science")

    def test_domain_other_dropped_when_not_others(self):
        sheet = FakeSheet()
        self.submit(sheet, domain="IT", domain_other="ignored", liked_topic_other="x")
        self.assertEqual((sheet.payloads[0]["domain_other"], sheet.payloads[0]["liked_topic_other"]), ("", ""))

    def test_liked_topic_other_is_optional(self):
        self.assertEqual(self.submit(FakeSheet(), liked_topic="Other").status_code, 201)

    def test_indian_mobile_normalisation(self):
        self.assertEqual(normalize_indian_mobile("98765 43210"), PHONE)
        self.assertEqual(normalize_indian_mobile("09876543210"), PHONE)
        self.assertEqual(normalize_indian_mobile("919876543210"), PHONE)
        self.assertIsNone(normalize_indian_mobile("5876543210"))
        self.assertIsNone(normalize_indian_mobile("98765"))
        self.assertIsNone(normalize_indian_mobile("abcdefghij"))


class FormContractTests(unittest.TestCase):
    """The page's wording may change, the API contract may not: every option the page offers must be a
    value the unchanged schema accepts, every accepted value must still be offered, and each one must
    go through the endpoint to the sheet."""

    FIELDS = ["domain", "overall_session", "usefulness", "learned_something", "liked_topic", "attend_future",
              "contact_preference"]

    def setUp(self):
        from pathlib import Path
        import re
        html = (Path(__file__).resolve().parent.parent.parent / "arckenites-orange" / "inauguration-feedback.html").read_text(encoding="utf-8")
        self.offered = {}
        for name, value in re.findall(r'<input type="radio" name="([a-z_]+)" value="([^"]+)">', html):
            self.offered.setdefault(name, []).append(value)

    def test_offered_values_equal_the_accepted_values(self):
        from typing import get_args
        from app.schemas.inauguration_feedback import InaugurationFeedbackRequest
        self.assertEqual(set(self.offered), set(self.FIELDS))
        for field in self.FIELDS:
            accepted = list(get_args(InaugurationFeedbackRequest.model_fields[field].annotation))
            self.assertEqual(sorted(self.offered[field]), sorted(accepted), field)

    def test_every_offered_value_is_accepted_by_the_endpoint_and_reaches_the_sheet(self):
        limiter.enabled = False
        client = TestClient(app)
        with mock.patch.object(settings, "feedback_sheets_webapp_url", "https://script.google.com/macros/s/FAKE/exec"), \
                mock.patch.object(settings, "feedback_sheets_shared_secret", SECRET):
            for field in self.FIELDS:
                for value in self.offered[field]:
                    over = {field: value}
                    if field == "domain" and value == "Others":
                        over["domain_other"] = "Data Science"
                    if field == "contact_preference" and value in ("yes", "maybe"):
                        over["contact_number"] = PHONE
                    sheet = FakeSheet()
                    with mock.patch.object(route, "post_to_sheet", sheet):
                        res = client.post(URL, json=valid(**over))
                    self.assertEqual(res.status_code, 201, f"{field}={value}")
                    self.assertEqual(len(sheet.payloads), 1)
        limiter.enabled = True


class FeedbackRateLimitTests(unittest.TestCase):
    """The 10/minute limit is unchanged; its 429 must reach the browser (CORS) with a clear message."""

    ORIGIN = "http://127.0.0.1:5500"

    def setUp(self):
        limiter.enabled = True
        limiter.reset()
        self.client = TestClient(app, raise_server_exceptions=False)
        self.patches = [
            mock.patch.object(settings, "feedback_sheets_webapp_url", "https://script.google.com/macros/s/FAKE/exec"),
            mock.patch.object(settings, "feedback_sheets_shared_secret", SECRET),
            mock.patch.object(route, "post_to_sheet", FakeSheet()),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        limiter.reset()

    def test_eleventh_request_gets_a_clear_429_with_cors_headers_and_no_database(self):
        def no_db():
            raise AssertionError("the feedback 429 must not touch the database")

        with mock.patch("app.main.SessionLocal", no_db):
            codes = [self.client.post(URL, json=valid(), headers={"Origin": self.ORIGIN}).status_code for _ in range(10)]
            self.assertEqual(codes, [201] * 10, "the limit itself is unchanged: 10 per minute")
            res = self.client.post(URL, json=valid(), headers={"Origin": self.ORIGIN})
        self.assertEqual(res.status_code, 429)
        self.assertEqual(res.headers.get("access-control-allow-origin"), self.ORIGIN)
        self.assertEqual(res.headers.get("retry-after"), "60")
        self.assertEqual(res.json()["detail"], "You have sent feedback several times in a short while. Please wait a minute and try again.")

    def test_429_for_a_disallowed_origin_gets_no_cors_header(self):
        for _ in range(10):
            self.client.post(URL, json=valid())
        res = self.client.post(URL, json=valid(), headers={"Origin": "https://evil.example"})
        self.assertEqual(res.status_code, 429)
        self.assertIsNone(res.headers.get("access-control-allow-origin"))

    def test_other_routes_audit_the_429_but_a_database_failure_cannot_turn_it_into_a_500(self):
        import asyncio
        from starlette.requests import Request
        from app import main

        def make_request():
            return Request({"type": "http", "method": "POST", "path": "/api/auth/login", "query_string": b"",
                            "headers": [(b"user-agent", b"unit-test")], "client": ("203.0.113.9", 1234)})

        with mock.patch.object(main, "SessionLocal") as session, mock.patch.object(main, "write_audit_event") as audit:
            res = asyncio.run(main.rate_limit_handler(make_request(), mock.Mock()))
        self.assertEqual(res.status_code, 429)
        self.assertEqual(audit.call_count, 1, "non-feedback routes still write their audit event")
        session.return_value.close.assert_called_once()

        with mock.patch.object(main, "SessionLocal", side_effect=RuntimeError("database down")):
            with self.assertLogs("rate_limit", level="ERROR") as logs:
                res = asyncio.run(main.rate_limit_handler(make_request(), mock.Mock()))
        self.assertEqual(res.status_code, 429)
        self.assertNotIn("203.0.113.9", "\n".join(logs.output))


if __name__ == "__main__":
    unittest.main()
