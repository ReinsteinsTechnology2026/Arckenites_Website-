import unittest
from unittest.mock import Mock

from fastapi.testclient import TestClient

from app.database import get_db
from app.main import app


class ReadinessTests(unittest.TestCase):
    def test_readiness_requires_database_query(self):
        db = Mock()
        old = app.dependency_overrides.copy()
        app.dependency_overrides[get_db] = lambda: db
        try:
            client = TestClient(app)  # No lifespan/background workers.
            response = client.get("/api/ready")
            client.close()
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), {"status": "ready"})
            db.execute.assert_called_once()
        finally:
            app.dependency_overrides.clear()
            app.dependency_overrides.update(old)

    def test_database_failure_returns_secret_safe_503(self):
        db = Mock()
        db.execute.side_effect = RuntimeError("private database endpoint")
        old = app.dependency_overrides.copy()
        app.dependency_overrides[get_db] = lambda: db
        try:
            client = TestClient(app)  # No lifespan/background workers.
            response = client.get("/api/ready")
            client.close()
            self.assertEqual(response.status_code, 503)
            self.assertNotIn("private database endpoint", response.text)
        finally:
            app.dependency_overrides.clear()
            app.dependency_overrides.update(old)
