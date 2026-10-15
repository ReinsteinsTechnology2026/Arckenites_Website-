"""Unit tests for Google sign-in's state/nonce cookie handling and
id_token claim validation (app/core/google_auth.py).

No real network request to Google is ever made. The one external
dependency — fetching Google's JWKS to get the id_token's signing key — is
mocked by generating our own RSA key pair, signing a test id_token with
its private key, and patching `_signing_keys()` to hand back an object
exposing that key pair's public half, exactly like PyJWKClient would for a
real Google key after a real network fetch. Every *validation* code path
(issuer, audience, nonce, expiry, email_verified, signature) is still the
real, unmodified production logic.
"""

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from app.config import settings
from app.core import google_auth

TEST_CLIENT_ID = "test-client-id.apps.googleusercontent.com"

_PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_PUBLIC_KEY = _PRIVATE_KEY.public_key()


def make_id_token(**claim_overrides) -> str:
    now = datetime.now(timezone.utc)
    claims = {
        "iss": "https://accounts.google.com",
        "aud": TEST_CLIENT_ID,
        "sub": "1234567890",
        "email": "Student@Example.com",
        "email_verified": True,
        "nonce": "expected-nonce",
        "iat": now,
        "exp": now + timedelta(minutes=5),
    }
    claims.update(claim_overrides)
    return jwt.encode(claims, _PRIVATE_KEY, algorithm="RS256")


class _FakeSigningKey:
    def __init__(self, key):
        self.key = key


class _FakeJwksClient:
    """Stands in for PyJWKClient — the only part of verify_id_token that
    would otherwise reach the real network (Google's JWKS endpoint)."""

    def get_signing_key_from_jwt(self, token):
        return _FakeSigningKey(_PUBLIC_KEY)


def patched_google_client_id():
    return patch.object(settings, "google_client_id", TEST_CLIENT_ID)


def patched_signing_keys():
    return patch.object(google_auth, "_signing_keys", return_value=_FakeJwksClient())


class TestStateCookie(unittest.TestCase):
    """make_state_cookie / read_state_cookie: signed, tamper-evident,
    round-trips the real state+nonce pair."""

    def test_round_trip_returns_the_same_state_and_nonce(self):
        cookie = google_auth.make_state_cookie("state-123", "nonce-abc")
        state, nonce = google_auth.read_state_cookie(cookie)
        self.assertEqual(state, "state-123")
        self.assertEqual(nonce, "nonce-abc")

    def test_tampered_cookie_is_rejected(self):
        cookie = google_auth.make_state_cookie("state-123", "nonce-abc")
        tampered = cookie[:-4] + ("A" * 4 if not cookie.endswith("AAAA") else "BBBB")
        with self.assertRaises(google_auth.GoogleAuthError) as ctx:
            google_auth.read_state_cookie(tampered)
        self.assertEqual(ctx.exception.code, "state")

    def test_cookie_with_wrong_purpose_is_rejected(self):
        payload = {
            "purpose": "not_google_state",
            "state": "s",
            "nonce": "n",
            "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
        }
        forged = jwt.encode(payload, settings.jwt_secret, algorithm="HS256")
        with self.assertRaises(google_auth.GoogleAuthError) as ctx:
            google_auth.read_state_cookie(forged)
        self.assertEqual(ctx.exception.code, "state")

    def test_expired_state_cookie_is_rejected(self):
        payload = {
            "purpose": google_auth.STATE_PURPOSE,
            "state": "s",
            "nonce": "n",
            "exp": datetime.now(timezone.utc) - timedelta(seconds=1),
        }
        expired = jwt.encode(payload, settings.jwt_secret, algorithm="HS256")
        with self.assertRaises(google_auth.GoogleAuthError):
            google_auth.read_state_cookie(expired)

    def test_garbage_value_is_rejected_not_a_crash(self):
        with self.assertRaises(google_auth.GoogleAuthError):
            google_auth.read_state_cookie("not-a-jwt-at-all")


class TestVerifyIdToken(unittest.TestCase):
    """verify_id_token: issuer, audience, nonce, expiry, email_verified and
    signature checks — the claim-validation core of the Google login flow."""

    def test_valid_token_returns_sub_and_lowercased_email(self):
        token = make_id_token()
        with patched_google_client_id(), patched_signing_keys():
            result = google_auth.verify_id_token(token, expected_nonce="expected-nonce")
        self.assertEqual(result["sub"], "1234567890")
        self.assertEqual(result["email"], "student@example.com")

    def test_wrong_nonce_is_rejected(self):
        token = make_id_token(nonce="expected-nonce")
        with patched_google_client_id(), patched_signing_keys():
            with self.assertRaises(google_auth.GoogleAuthError) as ctx:
                google_auth.verify_id_token(token, expected_nonce="a-different-nonce")
        self.assertEqual(ctx.exception.code, "identity")

    def test_wrong_issuer_is_rejected(self):
        token = make_id_token(iss="https://evil.example.com")
        with patched_google_client_id(), patched_signing_keys():
            with self.assertRaises(google_auth.GoogleAuthError) as ctx:
                google_auth.verify_id_token(token, expected_nonce="expected-nonce")
        self.assertEqual(ctx.exception.code, "identity")

    def test_wrong_audience_is_rejected(self):
        token = make_id_token(aud="someone-elses-client-id")
        with patched_google_client_id(), patched_signing_keys():
            with self.assertRaises(google_auth.GoogleAuthError):
                google_auth.verify_id_token(token, expected_nonce="expected-nonce")

    def test_expired_token_is_rejected(self):
        now = datetime.now(timezone.utc)
        token = make_id_token(iat=now - timedelta(minutes=20), exp=now - timedelta(minutes=10))
        with patched_google_client_id(), patched_signing_keys():
            with self.assertRaises(google_auth.GoogleAuthError):
                google_auth.verify_id_token(token, expected_nonce="expected-nonce")

    def test_unverified_email_is_rejected(self):
        token = make_id_token(email_verified=False)
        with patched_google_client_id(), patched_signing_keys():
            with self.assertRaises(google_auth.GoogleAuthError) as ctx:
                google_auth.verify_id_token(token, expected_nonce="expected-nonce")
        self.assertEqual(ctx.exception.code, "unverified")

    def test_token_signed_by_a_different_key_is_rejected(self):
        """Simulates a forged/substituted token: signed with a key that is
        NOT the one verify_id_token is told to trust — must fail on
        signature verification, not merely on claim checks."""
        other_private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        now = datetime.now(timezone.utc)
        forged = jwt.encode(
            {
                "iss": "https://accounts.google.com",
                "aud": TEST_CLIENT_ID,
                "sub": "1234567890",
                "email": "student@example.com",
                "email_verified": True,
                "nonce": "expected-nonce",
                "iat": now,
                "exp": now + timedelta(minutes=5),
            },
            other_private_key,
            algorithm="RS256",
        )
        with patched_google_client_id(), patched_signing_keys():
            with self.assertRaises(google_auth.GoogleAuthError) as ctx:
                google_auth.verify_id_token(forged, expected_nonce="expected-nonce")
        self.assertEqual(ctx.exception.code, "identity")


class TestEnsureCanSignIn(unittest.TestCase):
    """ensure_can_sign_in: the same role/active/locked/maintenance gates as
    password login, exercised with lightweight fake user objects — no DB."""

    def _user(self, **overrides):
        from types import SimpleNamespace

        from app.models.user import RoleEnum

        defaults = dict(role=RoleEnum.student, is_active=True, locked_until=None)
        defaults.update(overrides)
        return SimpleNamespace(**defaults)

    def test_no_matching_account_is_rejected(self):
        with self.assertRaises(google_auth.GoogleAuthError) as ctx:
            google_auth.ensure_can_sign_in(None, maintenance_mode=False)
        self.assertEqual(ctx.exception.code, "no_account")

    def test_community_role_cannot_use_google_sign_in(self):
        from app.models.user import RoleEnum

        user = self._user(role=RoleEnum.community)
        with self.assertRaises(google_auth.GoogleAuthError) as ctx:
            google_auth.ensure_can_sign_in(user, maintenance_mode=False)
        self.assertEqual(ctx.exception.code, "no_account")

    def test_inactive_account_is_rejected(self):
        user = self._user(is_active=False)
        with self.assertRaises(google_auth.GoogleAuthError) as ctx:
            google_auth.ensure_can_sign_in(user, maintenance_mode=False)
        self.assertEqual(ctx.exception.code, "disabled")

    def test_locked_account_is_rejected(self):
        user = self._user(locked_until=datetime.now(timezone.utc) + timedelta(minutes=5))
        with self.assertRaises(google_auth.GoogleAuthError) as ctx:
            google_auth.ensure_can_sign_in(user, maintenance_mode=False)
        self.assertEqual(ctx.exception.code, "locked")

    def test_expired_lock_does_not_block_sign_in(self):
        user = self._user(locked_until=datetime.now(timezone.utc) - timedelta(minutes=5))
        google_auth.ensure_can_sign_in(user, maintenance_mode=False)  # should not raise

    def test_maintenance_mode_blocks_non_admins(self):
        user = self._user()  # student
        with self.assertRaises(google_auth.GoogleAuthError) as ctx:
            google_auth.ensure_can_sign_in(user, maintenance_mode=True)
        self.assertEqual(ctx.exception.code, "maintenance")

    def test_maintenance_mode_allows_admins(self):
        from app.models.user import RoleEnum

        user = self._user(role=RoleEnum.admin)
        google_auth.ensure_can_sign_in(user, maintenance_mode=True)  # should not raise


if __name__ == "__main__":
    unittest.main()
