import unittest

from pydantic import ValidationError

from app.core.security import hash_password, verify_password
from app.schemas.admin_staff import CreateStaffRequest, UpdateStaffRequest
from app.schemas.admin_users import CreateAdminUserRequest, UpdateAdminUserRequest
from app.schemas.community import SetCommunityPasswordRequest
from app.schemas.meeting import CreateMeetingRequest, UpdateMeetingRequest


class PasswordBoundaryTests(unittest.TestCase):
    def test_bcrypt_never_accepts_a_different_suffix_after_72_bytes(self):
        password = "A" * 72
        digest = hash_password(password)
        self.assertTrue(verify_password(password, digest))
        self.assertFalse(verify_password(password + "Z", digest))
        with self.assertRaisesRegex(ValueError, "72 UTF-8 bytes"):
            hash_password(password + "Z")

    def test_account_and_meeting_schemas_reject_overlong_utf8_passwords(self):
        password = "é" * 37  # 74 UTF-8 bytes but only 37 characters.
        cases = [
            (CreateStaffRequest, {"full_name": "Trainer", "email": "trainer@example.test",
                                  "temp_password": password}),
            (UpdateStaffRequest, {"reset_temp_password": password}),
            (CreateAdminUserRequest, {"full_name": "Admin", "admin_role_id": 1, "temp_password": password}),
            (UpdateAdminUserRequest, {"reset_temp_password": password}),
            (SetCommunityPasswordRequest, {"verification_token": "1234567890",
                                           "new_password": password, "confirm_password": password}),
            (CreateMeetingRequest, {"title": "Meeting", "host_id": 1,
                                    "scheduled_at": "2026-10-17T00:00:00Z", "password": password}),
            (UpdateMeetingRequest, {"password": password}),
        ]
        for schema, payload in cases:
            with self.subTest(schema=schema.__name__), self.assertRaises(ValidationError):
                schema.model_validate(payload)
