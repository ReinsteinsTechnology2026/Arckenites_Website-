"""The standalone feedback API: only the feedback endpoint, no database, same behaviour as the main app."""
import unittest
import uuid
from unittest import mock

from fastapi.testclient import TestClient

from app.api import routes_inauguration_feedback as route
from app.config import settings
from app.core import feedback_sheets as fs
from app.core.rate_limit import limiter

# The service refuses to import unless DATABASE_URL is the sqlite placeholder (its production setting).
# CI runs the whole suite against PostgreSQL, so give the import the same value the service has in production;
# nothing here ever opens a connection (see test_no_database_connection_is_attempted_for_any_outcome).
with mock.patch.object(settings, "database_url", "sqlite://"):
    from app import feedback_app

URL = "/api/feedback/inauguration"
SECRET = "test-shared-secret-not-real"


def body(**over):
    b = {"submission_id": str(uuid.uuid4()), "name": "Asha", "domain": "Cloud", "overall_session": "Excellent",
         "usefulness": "Very Useful", "learned_something": "Yes", "liked_topic": "AI", "attend_future": "Yes",
         "contact_preference": "no"}
    b.update(over)
    return b


class FeedbackAppTests(unittest.TestCase):
    def setUp(self):
        limiter.enabled = True
        limiter.reset()
        self.client = TestClient(feedback_app.app, raise_server_exceptions=False)
        self.patches = [
            mock.patch.object(settings, "feedback_sheets_webapp_url", "https://script.google.com/macros/s/FAKE/exec"),
            mock.patch.object(settings, "feedback_sheets_shared_secret", SECRET),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        limiter.reset()

    def test_only_the_feedback_endpoint_and_health_exist(self):
        paths = {(m, r.path) for r in feedback_app.app.routes for m in (getattr(r, "methods", None) or [])
                 if m not in ("HEAD", "OPTIONS")}
        self.assertEqual(paths, {("POST", URL), ("GET", "/api/health")})
        self.assertEqual(self.client.get("/api/health").json(), {"status": "ok"})
        for path in ("/docs", "/redoc", "/openapi.json", "/api/auth/login", "/api/students", "/", "/login.html"):
            self.assertEqual(self.client.get(path).status_code, 404, path)

    def test_submission_reaches_the_sheet_and_confirms(self):
        sent = []
        with mock.patch.object(route, "post_to_sheet", lambda p: sent.append(p) or "appended"):
            res = self.client.post(URL, json=body(contact_preference="yes", contact_number="9876543210"))
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.json()["detail"], "Thank you for your valuable feedback!")
        self.assertEqual((sent[0]["contact_preference"], sent[0]["contact_number"]), ("Yes", "9876543210"))

    def test_sheet_failure_is_a_retryable_503_not_success(self):
        def down(_):
            raise fs.SheetsError("timeout")

        with mock.patch.object(route, "post_to_sheet", down):
            res = self.client.post(URL, json=body())
        self.assertEqual(res.status_code, 503)
        self.assertEqual(res.json()["detail"], route.RETRY_MESSAGE)

    def test_never_opens_a_database_session(self):
        def no_db(*a, **k):
            raise AssertionError("the feedback app must not touch the database")

        with mock.patch("app.database.SessionLocal", no_db), mock.patch.object(route, "post_to_sheet", lambda p: "appended"):
            self.assertEqual(self.client.post(URL, json=body()).status_code, 201)
            for _ in range(10):  # trip the rate limit too: the 429 handler must not use the database either
                res = self.client.post(URL, json=body())
        self.assertEqual(res.status_code, 429)

    def test_rate_limit_is_10_per_minute_with_a_clear_429(self):
        with mock.patch.object(route, "post_to_sheet", lambda p: "appended"):
            codes = [self.client.post(URL, json=body(), headers={"X-Forwarded-For": "203.0.113.7"}).status_code for _ in range(11)]
        self.assertEqual(codes, [201] * 10 + [429])
        res = self.client.post(URL, json=body())
        self.assertEqual(res.status_code, 429)
        self.assertEqual(res.headers["retry-after"], "60")
        self.assertEqual(res.json()["detail"], feedback_app.RATE_LIMIT_MESSAGE)

    def test_no_database_connection_is_attempted_for_any_outcome(self):
        """Stronger than patching SessionLocal: any attempt to open a connection at all fails the test."""
        def boom(*a, **k):
            raise AssertionError("a database connection was attempted")

        with mock.patch("sqlalchemy.engine.base.Engine.connect", boom), \
                mock.patch("sqlalchemy.engine.base.Engine.raw_connection", boom), \
                mock.patch("sqlalchemy.engine.base.Engine.begin", boom):
            with mock.patch.object(route, "post_to_sheet", lambda p: "appended"):
                self.assertEqual(self.client.post(URL, json=body()).status_code, 201)
            with mock.patch.object(route, "post_to_sheet", lambda p: (_ for _ in ()).throw(fs.SheetsError("timeout"))):
                self.assertEqual(self.client.post(URL, json=body()).status_code, 503)
            with mock.patch.object(settings, "feedback_sheets_webapp_url", ""):
                self.assertEqual(self.client.post(URL, json=body()).status_code, 503)
            self.assertEqual(self.client.get("/api/health").status_code, 200)

    def test_refuses_to_start_with_a_real_database_url_and_never_prints_it(self):
        """If a production .env were picked up, the service must not start."""
        import os, subprocess, sys
        from pathlib import Path
        secret_url = "postgresql+psycopg://svc_user:Sup3rS3cretPw@10.0.0.5:5432/proddb"
        env = {**os.environ, "DATABASE_URL": secret_url, "JWT_SECRET": "x" * 32, "ENV": "test", "PYTHONDONTWRITEBYTECODE": "1"}
        backend = Path(__file__).resolve().parent.parent
        res = subprocess.run([sys.executable, "-c", "import app.feedback_app"], cwd=backend, env=env, capture_output=True, text=True)
        self.assertNotEqual(res.returncode, 0)
        output = res.stdout + res.stderr
        self.assertIn("Refusing to start", output)
        for leaked in ("Sup3rS3cretPw", "svc_user", "10.0.0.5", "proddb"):
            self.assertNotIn(leaked, output)
        env["DATABASE_URL"] = "sqlite://"
        ok = subprocess.run([sys.executable, "-c", "import app.feedback_app"], cwd=backend, env=env, capture_output=True, text=True)
        self.assertEqual(ok.returncode, 0, ok.stderr[-400:])

    def test_module_has_no_authentication_or_database_dependencies_wired_in(self):
        """No login/JWT/DB-session dependency is registered anywhere in this app."""
        for r in feedback_app.app.routes:
            dependant = getattr(r, "dependant", None)
            if dependant is not None:
                self.assertEqual(dependant.dependencies, [], r.path)
        self.assertEqual(feedback_app.app.user_middleware[0].cls.__name__, "SlowAPIMiddleware")
        self.assertEqual(len(feedback_app.app.user_middleware), 1)

    def test_429_wording_matches_the_main_api(self):
        from app import main
        self.assertEqual(feedback_app.RATE_LIMIT_MESSAGE, main.FEEDBACK_RATE_LIMIT_MESSAGE)

    def test_invalid_submission_is_rejected_before_the_sheet(self):
        sent = []
        with mock.patch.object(route, "post_to_sheet", lambda p: sent.append(p) or "appended"):
            self.assertEqual(self.client.post(URL, json=body(submission_id="nope")).status_code, 422)
            self.assertEqual(self.client.post(URL, json={"name": "x"}).status_code, 422)
        self.assertEqual(sent, [])


if __name__ == "__main__":
    unittest.main()
