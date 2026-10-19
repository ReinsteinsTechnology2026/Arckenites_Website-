import json
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi import HTTPException
from pydantic import ValidationError

from app.api import routes_students
from app.schemas.auth import UpdateMyProfileRequest


class StudentAccountTests(unittest.TestCase):
    def user(self, *, user_id=7):
        profile = SimpleNamespace(email="student@example.test", phone="1234567890", address="Old")
        return SimpleNamespace(id=user_id, full_name="Student", username="student@example.test",
                               student_id="AK-7", email=profile.email, phone=profile.phone,
                               address=profile.address, email_notifications_enabled=True,
                               student_profile=profile)

    def test_profile_rejects_contact_changes_and_protected_fields(self):
        user = self.user()
        db = MagicMock()
        payload = UpdateMyProfileRequest(full_name="Student", email="other@example.test",
                                         phone=user.phone, address="New")
        with self.assertRaises(HTTPException) as raised:
            routes_students.update_my_profile(payload, db=db, user=user)
        self.assertEqual(raised.exception.status_code, 403)
        db.commit.assert_not_called()
        with self.assertRaises(ValidationError):
            UpdateMyProfileRequest(full_name="Student", email=user.email, role="admin")
        with self.assertRaises(ValidationError):
            UpdateMyProfileRequest(full_name="   ", email=user.email)

    def test_profile_updates_only_name_and_address(self):
        user = self.user()
        db = MagicMock()
        payload = UpdateMyProfileRequest(full_name="  New Name  ", email=user.email,
                                         phone=user.phone, address="New address")
        with patch.object(routes_students.MeResponse, "model_validate", return_value={"ok": True}):
            routes_students.update_my_profile(payload, db=db, user=user)
        self.assertEqual(user.full_name, "New Name")
        self.assertEqual(user.student_profile.address, "New address")
        self.assertEqual(user.student_profile.email, "student@example.test")
        db.commit.assert_called_once()

    def test_enrollment_queries_use_authenticated_user_id(self):
        db = MagicMock()
        db.execute.return_value.all.return_value = []
        self.assertEqual(routes_students.my_enrollments(db=db, user=self.user(user_id=37)),
                         {"programs": [], "batches": []})
        self.assertEqual(db.execute.call_count, 2)
        for call in db.execute.call_args_list:
            self.assertIn(37, call.args[0].compile().params.values())

    def test_login_history_is_bounded_and_owned(self):
        db = MagicMock()
        db.scalars.return_value.all.return_value = []
        result = routes_students.my_login_history(page=1, db=db, user=self.user(user_id=37))
        self.assertEqual(result["items"], [])
        statement = db.scalars.call_args.args[0]
        self.assertIn(37, statement.compile().params.values())
        self.assertIn(21, statement.compile().params.values())

    def test_export_excludes_secrets_and_sets_download_headers(self):
        user = self.user()
        user.password_hash = "secret-hash"
        user.mfa_secret = "secret-mfa"
        with patch.object(routes_students, "my_enrollments", return_value={"programs": [], "batches": []}):
            response = routes_students.download_my_data(db=MagicMock(), user=user)
        data = json.loads(response.body)
        self.assertEqual(data["profile"]["student_id"], "AK-7")
        self.assertNotIn("password_hash", response.body.decode())
        self.assertNotIn("secret-mfa", response.body.decode())
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertIn("attachment", response.headers["content-disposition"])


if __name__ == "__main__":
    unittest.main()
