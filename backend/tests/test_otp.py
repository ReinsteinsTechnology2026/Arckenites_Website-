"""Unit tests for the Community OTP flow: generation, hashing, expiry,
attempt limits, rate limiting and single-use behavior.

Exercises the real production code (app/core/otp.py and the business-logic
helpers in app/api/routes_community_auth.py) with fakes/mocks standing in
for the database and the outbound email send. No real email is sent, no
real database is touched, and no production credentials are used.
"""

import inspect
import re
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi import HTTPException

from app.api import routes_community_auth as rca
from app.core import otp as otp_module
from app.models.community import CommunityRegistrationStatus
from app.schemas.community import VerifyCommunityOtpRequest

# The real route functions are wrapped by slowapi's rate-limit decorator;
# inspect.unwrap reaches the actual, undecorated function so it can be
# called directly in a test without a live ASGI app or ip-based limiter state.
verify_otp_endpoint = inspect.unwrap(rca.verify_otp_endpoint)


def make_registration(**overrides) -> SimpleNamespace:
    """A plain stand-in for a CommunityRegistration row. The functions under
    test only ever touch plain attributes on it, never SQLAlchemy-specific
    behavior, so a SimpleNamespace is a faithful, isolated substitute."""
    now = datetime.now(timezone.utc)
    defaults = dict(
        email="student@example.com",
        full_name="Test Student",
        status=CommunityRegistrationStatus.pending,
        otp_hash=None,
        otp_expires_at=None,
        otp_attempts=0,
        otp_window_started_at=None,
        otp_send_count=0,
        verified_at=None,
        verification_token_hash=None,
        verification_token_expires_at=None,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def make_request() -> MagicMock:
    request = MagicMock()
    request.client.host = "127.0.0.1"
    request.headers.get.return_value = "pytest-agent"
    return request


class TestOtpPrimitives(unittest.TestCase):
    """app/core/otp.py's pure functions, called directly."""

    def test_generate_otp_is_six_numeric_digits(self):
        for _ in range(20):
            code = otp_module.generate_otp()
            self.assertEqual(len(code), 6)
            self.assertRegex(code, r"^\d{6}$")

    def test_generate_otp_is_not_derived_from_other_data(self):
        # Not a strict uniqueness guarantee (it's a 6-digit space), but two
        # consecutive real crypto-random draws should essentially never
        # collide in a 20-sample run.
        codes = {otp_module.generate_otp() for _ in range(20)}
        self.assertGreater(len(codes), 1)

    def test_hash_and_verify_roundtrip(self):
        code = "123456"
        hashed = otp_module.hash_otp(code)
        self.assertTrue(otp_module.verify_otp(code, hashed))

    def test_verify_rejects_wrong_code(self):
        hashed = otp_module.hash_otp("123456")
        self.assertFalse(otp_module.verify_otp("654321", hashed))

    def test_otp_expiry_is_ten_minutes_out(self):
        before = datetime.now(timezone.utc)
        expiry = otp_module.otp_expiry()
        delta = expiry - before
        self.assertTrue(timedelta(minutes=9, seconds=55) < delta <= timedelta(minutes=10, seconds=5))

    def test_mask_email_keeps_domain_hides_most_of_local_part(self):
        self.assertEqual(otp_module.mask_email("su@gmail.com"), "su****@gmail.com")
        masked = otp_module.mask_email("suhaib27@example.com")
        self.assertTrue(masked.startswith("su"))
        self.assertTrue(masked.endswith("@example.com"))
        self.assertNotIn("haib27", masked)


class TestIssueOtp(unittest.TestCase):
    """_issue_otp: rate limiting, send-failure handling, and successful
    issuance, exercised against the real function with a mocked db and a
    mocked email send (never a real SMTP/Graph call)."""

    def test_rate_limited_when_window_send_count_exhausted(self):
        now = datetime.now(timezone.utc)
        reg = make_registration(
            otp_window_started_at=now,
            otp_send_count=otp_module.OTP_MAX_SENDS_PER_WINDOW,
        )
        db = MagicMock()
        with patch.object(rca, "_send_otp_email") as send_mock:
            result = rca._issue_otp(db, reg, reg.full_name)
        self.assertEqual(result, rca.OtpIssueResult.RATE_LIMITED)
        send_mock.assert_not_called()
        self.assertIsNone(reg.otp_hash)  # no fresh code issued while rate-limited

    def test_failed_send_does_not_leave_a_usable_otp_hash(self):
        """The bug this code explicitly guards against: a failed send must
        never leave behind an OTP hash the user has no way to receive."""
        reg = make_registration()
        db = MagicMock()
        with patch.object(rca, "_send_otp_email", return_value=False) as send_mock:
            result = rca._issue_otp(db, reg, reg.full_name)
        self.assertEqual(result, rca.OtpIssueResult.FAILED)
        send_mock.assert_called_once()
        self.assertIsNone(reg.otp_hash)
        self.assertEqual(reg.otp_send_count, 1)  # still counts toward the window

    def test_successful_send_sets_hash_expiry_and_resets_attempts(self):
        reg = make_registration(otp_attempts=3)  # simulate prior failed verify attempts
        db = MagicMock()
        with patch.object(rca, "_send_otp_email", return_value=True) as send_mock:
            result = rca._issue_otp(db, reg, reg.full_name)
        self.assertEqual(result, rca.OtpIssueResult.SENT)
        send_mock.assert_called_once()
        self.assertIsNotNone(reg.otp_hash)
        self.assertIsNotNone(reg.otp_expires_at)
        self.assertEqual(reg.otp_attempts, 0)
        self.assertEqual(reg.status, CommunityRegistrationStatus.pending)


class TestVerifyOtpEndpoint(unittest.TestCase):
    """verify_otp_endpoint: expiry, attempt-limit lockout, wrong-code
    handling, and single-use invalidation on success — called directly
    (via inspect.unwrap) against a mocked db, never a real database."""

    def _call(self, reg, otp="123456", email="student@example.com"):
        db = MagicMock()
        db.scalar.return_value = reg
        request = make_request()
        payload = VerifyCommunityOtpRequest(email=(reg.email if reg is not None else email), otp=otp)
        return verify_otp_endpoint(request=request, payload=payload, db=db), db

    def test_no_pending_registration_is_rejected_generically(self):
        with self.assertRaises(HTTPException) as ctx:
            self._call(None)
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertEqual(ctx.exception.detail, rca.GENERIC_OTP_ERROR)

    def test_expired_code_is_rejected(self):
        reg = make_registration(
            otp_hash=otp_module.hash_otp("123456"),
            otp_expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
        )
        with self.assertRaises(HTTPException) as ctx:
            self._call(reg, otp="123456")
        self.assertEqual(ctx.exception.status_code, 400)

    def test_already_exhausted_attempts_are_rejected_and_hash_cleared_defensively(self):
        reg = make_registration(
            otp_hash=otp_module.hash_otp("123456"),
            otp_expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
            otp_attempts=otp_module.OTP_MAX_ATTEMPTS,
        )
        with self.assertRaises(HTTPException):
            self._call(reg, otp="123456")
        self.assertIsNone(reg.otp_hash)
        self.assertIsNone(reg.otp_expires_at)

    def test_wrong_code_increments_attempts_without_succeeding(self):
        reg = make_registration(
            otp_hash=otp_module.hash_otp("123456"),
            otp_expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        )
        with self.assertRaises(HTTPException):
            self._call(reg, otp="000000")
        self.assertEqual(reg.otp_attempts, 1)
        self.assertIsNotNone(reg.otp_hash)  # still usable — not yet at the limit

    def test_reaching_max_attempts_on_a_wrong_code_invalidates_the_otp(self):
        reg = make_registration(
            otp_hash=otp_module.hash_otp("123456"),
            otp_expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
            otp_attempts=otp_module.OTP_MAX_ATTEMPTS - 1,
        )
        with self.assertRaises(HTTPException):
            self._call(reg, otp="000000")
        self.assertEqual(reg.otp_attempts, otp_module.OTP_MAX_ATTEMPTS)
        self.assertIsNone(reg.otp_hash)

    def test_correct_code_succeeds_and_returns_a_verification_token(self):
        reg = make_registration(
            otp_hash=otp_module.hash_otp("123456"),
            otp_expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        )
        result, _db = self._call(reg, otp="123456")
        self.assertTrue(result.verification_token)
        self.assertEqual(reg.status, CommunityRegistrationStatus.verified)
        self.assertIsNotNone(reg.verified_at)

    def test_correct_code_is_single_use(self):
        """The OTP hash is cleared immediately on success, so replaying the
        same correct code a second time must fail, not succeed again."""
        reg = make_registration(
            otp_hash=otp_module.hash_otp("123456"),
            otp_expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        )
        self._call(reg, otp="123456")  # first use: succeeds
        self.assertIsNone(reg.otp_hash)
        with self.assertRaises(HTTPException) as ctx:
            self._call(reg, otp="123456")  # replay: must be rejected
        self.assertEqual(ctx.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()
