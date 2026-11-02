"""Inauguration feedback endpoint: server-side validation and real persistence (isolated SQLite)."""
import unittest

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.rate_limit import limiter
from app.database import get_db
from app.main import app
from app.models.inauguration_feedback import InaugurationFeedback
from app.schemas.inauguration_feedback import normalize_indian_mobile

URL = "/api/feedback/inauguration"


def valid(**over):
    body = {
        "name": "Asha",
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


class InaugurationFeedbackTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        InaugurationFeedback.__table__.create(self.engine)

        def override():
            with Session(self.engine) as db:
                yield db

        app.dependency_overrides[get_db] = override
        limiter.enabled = False
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.pop(get_db, None)
        limiter.enabled = True
        self.engine.dispose()

    def rows(self):
        with Session(self.engine) as db:
            return db.scalars(select(InaugurationFeedback)).all()

    def test_valid_submission_is_stored(self):
        res = self.client.post(URL, json=valid(suggestion="  More labs  "))
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.json()["detail"], "Thank you for your valuable feedback!")
        row = self.rows()[0]
        self.assertEqual((row.name, row.domain, row.suggestion), ("Asha", "Cloud", "More labs"))
        self.assertIsNone(row.contact_number)

    def test_missing_required_fields_rejected(self):
        for field in ("name", "domain", "overall_session", "usefulness", "learned_something",
                      "liked_topic", "attend_future", "contact_preference"):
            body = valid()
            del body[field]
            self.assertEqual(self.client.post(URL, json=body).status_code, 422, field)
        self.assertEqual(self.client.post(URL, json=valid(name="   ")).status_code, 422)
        self.assertEqual(self.rows(), [])

    def test_invalid_choice_rejected(self):
        self.assertEqual(self.client.post(URL, json=valid(overall_session="Terrible")).status_code, 422)

    def test_others_domain_requires_text(self):
        self.assertEqual(self.client.post(URL, json=valid(domain="Others")).status_code, 422)
        res = self.client.post(URL, json=valid(domain="Others", domain_other="Data Science"))
        self.assertEqual(res.status_code, 201)
        self.assertEqual(self.rows()[0].domain_other, "Data Science")

    def test_domain_other_dropped_when_not_others(self):
        self.client.post(URL, json=valid(domain="IT", domain_other="ignored", liked_topic_other="x"))
        row = self.rows()[0]
        self.assertIsNone(row.domain_other)
        self.assertIsNone(row.liked_topic_other)

    def test_liked_topic_other_is_optional(self):
        self.assertEqual(self.client.post(URL, json=valid(liked_topic="Other")).status_code, 201)

    def test_phone_required_for_yes_and_maybe(self):
        for pref in ("yes", "maybe"):
            self.assertEqual(self.client.post(URL, json=valid(contact_preference=pref)).status_code, 422)
            self.assertEqual(
                self.client.post(URL, json=valid(contact_preference=pref, contact_number="12345")).status_code, 422
            )
        res = self.client.post(URL, json=valid(contact_preference="yes", contact_number="+91 98765-43210"))
        self.assertEqual(res.status_code, 201)
        self.assertEqual(self.rows()[0].contact_number, "9876543210")

    def test_phone_not_stored_when_declined(self):
        self.client.post(URL, json=valid(contact_preference="no", contact_number="9876543210"))
        self.assertIsNone(self.rows()[0].contact_number)

    def test_indian_mobile_normalisation(self):
        self.assertEqual(normalize_indian_mobile("98765 43210"), "9876543210")
        self.assertEqual(normalize_indian_mobile("09876543210"), "9876543210")
        self.assertEqual(normalize_indian_mobile("919876543210"), "9876543210")
        self.assertIsNone(normalize_indian_mobile("5876543210"))
        self.assertIsNone(normalize_indian_mobile("98765"))
        self.assertIsNone(normalize_indian_mobile("abcdefghij"))


if __name__ == "__main__":
    unittest.main()
