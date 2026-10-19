"""Isolated HTTP/ORM regressions for TOTP MFA; no external DB or email."""
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session

from app.config import settings
from app.core.mfa import decrypt_secret, totp_for_step
from app.core.rate_limit import limiter
from app.core.security import create_access_token, hash_password
from app.database import Base, get_db
from app.main import app
from app.models.admin_role import AdminRole
from app.models.google_identity import GoogleLoginTicket
from app.models.mfa import MfaChallenge, MfaCredential, MfaRecoveryCode
from app.models.session import AuthSession
from app.models.system_settings import SystemSettings
from app.models.user import RoleEnum, User


@compiles(JSONB, "sqlite")
def _jsonb_for_qa(type_, compiler, **kwargs):
    return "JSON"


PASSWORD = "Test#MfaPassword123"


class QaSession(Session):
    pass


@event.listens_for(QaSession, "loaded_as_persistent")
def _utc_loaded(session, instance):
    if isinstance(instance, (AuthSession, GoogleLoginTicket)) and instance.expires_at.tzinfo is None:
        instance.expires_at = instance.expires_at.replace(tzinfo=timezone.utc)


class MfaHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="arckenites-mfa-qa-")
        self.engine = create_engine(f"sqlite:///{Path(self.temp.name) / 'qa.sqlite'}",
                                    connect_args={"check_same_thread": False})
        @event.listens_for(self.engine, "connect")
        def foreign_keys(connection, record):
            connection.execute("PRAGMA foreign_keys=ON")
        Base.metadata.create_all(self.engine)
        with QaSession(self.engine) as db:
            role = AdminRole(name="Super Admin", slug="super_admin", is_system=True)
            db.add(role)
            db.flush()
            for kind in (RoleEnum.admin, RoleEnum.staff, RoleEnum.student, RoleEnum.community):
                db.add(User(username=f"mfa-{kind.value}@example.test", password_hash=hash_password(PASSWORD),
                            full_name=f"MFA {kind.value}", role=kind,
                            admin_role_id=role.id if kind == RoleEnum.admin else None,
                            is_active=True, must_change_password=False))
            db.add(SystemSettings(id=1))
            db.commit()
            self.ids = {u.role.value: u.id for u in db.scalars(select(User)).all()}
        self.original_overrides = app.dependency_overrides.copy()
        app.dependency_overrides[get_db] = self._db
        self.client = TestClient(app)
        self.key_patch = patch.object(settings, "mfa_encryption_key", Fernet.generate_key().decode())
        self.key_patch.start()
        self.limiter_patch = patch.object(limiter, "enabled", False)
        self.limiter_patch.start()

    def tearDown(self):
        self.client.close()
        app.dependency_overrides.clear()
        app.dependency_overrides.update(self.original_overrides)
        self.limiter_patch.stop()
        self.key_patch.stop()
        self.engine.dispose()
        self.temp.cleanup()

    def _db(self):
        with QaSession(self.engine) as db:
            yield db

    def _headers(self, role):
        jti = uuid4()
        token, expires = create_access_token(self.ids[role], role, jti)
        with QaSession(self.engine) as db:
            db.add(AuthSession(id=jti, user_id=self.ids[role], expires_at=expires))
            db.commit()
        return {"Authorization": f"Bearer {token}"}

    def _login(self, role):
        return self.client.post("/api/auth/login", json={"username": f"mfa-{role}@example.test",
                                                         "password": PASSWORD})

    def _setup(self, role="student"):
        headers = self._headers(role)
        begin = self.client.post("/api/auth/mfa/setup/start", json={"password": PASSWORD}, headers=headers)
        self.assertEqual(begin.status_code, 200, begin.text)
        self.assertTrue(begin.json()["qr_code"].startswith("data:image/png;base64,"))
        with QaSession(self.engine) as db:
            row = db.get(MfaCredential, self.ids[role])
            self.assertIsNone(row.enabled_at)
            self.assertNotIn("otpauth", row.pending_ciphertext)
            secret = decrypt_secret(row.pending_ciphertext)
        code = totp_for_step(secret, int(datetime.now(timezone.utc).timestamp() // 30))
        confirm = self.client.post("/api/auth/mfa/setup/confirm", json={"code": code}, headers=headers)
        self.assertEqual(confirm.status_code, 200, confirm.text)
        return headers, confirm.json()["recovery_codes"]

    def test_mfa_disabled_keeps_existing_login_for_all_roles(self):
        for role in self.ids:
            with self.subTest(role=role):
                response = self._login(role)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertIn("access_token", response.json())

    def test_selected_portal_is_enforced_before_session_or_mfa(self):
        # Student can only use the student entry; staff and admin use staff.
        for role, portal, allowed in (
            ("student", "student", True), ("student", "staff", False),
            ("staff", "staff", True), ("staff", "student", False),
            ("admin", "staff", True), ("admin", "student", False),
        ):
            with self.subTest(role=role, portal=portal):
                response = self.client.post("/api/auth/login", json={
                    "username": f"mfa-{role}@example.test", "password": PASSWORD, "portal": portal,
                })
                self.assertEqual(response.status_code, 200 if allowed else 401, response.text)
                if allowed:
                    self.assertIn("access_token", response.json())
                    self.assertEqual(response.json()["user"]["role"], role)
                else:
                    self.assertNotIn("access_token", response.json())
                    self.assertNotIn("challenge", response.json())

    def test_setup_requires_confirm_then_password_login_is_challenged(self):
        old = self._headers("student")
        pending = self.client.post("/api/auth/mfa/setup/start", json={"password": PASSWORD}, headers=old)
        self.assertEqual(pending.status_code, 200, pending.text)
        earlier_token = self._login("student").json()["access_token"]
        with QaSession(self.engine) as db:
            secret = decrypt_secret(db.get(MfaCredential, self.ids["student"]).pending_ciphertext)
        code = totp_for_step(secret, int(datetime.now(timezone.utc).timestamp() // 30))
        confirmed = self.client.post("/api/auth/mfa/setup/confirm", json={"code": code}, headers=old)
        self.assertEqual(confirmed.status_code, 200, confirmed.text)
        self.assertEqual(len(confirmed.json()["recovery_codes"]), 8)
        self.assertEqual(self.client.get("/api/auth/me", headers=old).status_code, 200)
        self.assertEqual(self.client.get("/api/auth/me", headers={
            "Authorization": f"Bearer {earlier_token}"}).status_code, 401)
        challenged = self._login("student")
        self.assertEqual(challenged.status_code, 200, challenged.text)
        self.assertTrue(challenged.json()["mfa_required"])
        self.assertNotIn("access_token", challenged.json())
        self.assertEqual(self.client.post("/api/auth/mfa/challenge/verify", json={
            "challenge": challenged.json()["challenge"], "code": "111111"}).status_code, 401)
        # The setup code was consumed, so a fresh step or recovery code is required.
        recovery = confirmed.json()["recovery_codes"][0]
        completed = self.client.post("/api/auth/mfa/challenge/verify", json={
            "challenge": challenged.json()["challenge"], "code": recovery})
        self.assertEqual(completed.status_code, 200, completed.text)
        self.assertIn("access_token", completed.json())
        self.assertEqual(self.client.post("/api/auth/mfa/challenge/verify", json={
            "challenge": challenged.json()["challenge"], "code": recovery}).status_code, 401)

    def test_recovery_code_is_single_use_and_challenge_expires(self):
        headers, codes = self._setup()
        first = self._login("student").json()["challenge"]
        used = self.client.post("/api/auth/mfa/challenge/verify", json={"challenge": first, "code": codes[0]})
        self.assertEqual(used.status_code, 200, used.text)
        second = self._login("student").json()["challenge"]
        self.assertEqual(self.client.post("/api/auth/mfa/challenge/verify", json={
            "challenge": second, "code": codes[0]}).status_code, 401)
        with QaSession(self.engine) as db:
            row = db.scalar(select(MfaChallenge).where(MfaChallenge.token_hash.is_not(None)).order_by(MfaChallenge.id.desc()))
            row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            db.commit()
        self.assertEqual(self.client.post("/api/auth/mfa/challenge/verify", json={
            "challenge": second, "code": codes[1]}).status_code, 401)

    def test_valid_totp_completes_login_and_cannot_be_replayed(self):
        self._setup("student")
        with QaSession(self.engine) as db:
            secret = decrypt_secret(db.get(MfaCredential, self.ids["student"]).secret_ciphertext)
        step = int(datetime.now(timezone.utc).timestamp() // 30)
        next_code = totp_for_step(secret, step + 1)
        challenge = self._login("student").json()["challenge"]
        completed = self.client.post("/api/auth/mfa/challenge/verify", json={
            "challenge": challenge, "code": next_code})
        self.assertEqual(completed.status_code, 200, completed.text)
        self.assertIn("access_token", completed.json())
        second = self._login("student").json()["challenge"]
        self.assertEqual(self.client.post("/api/auth/mfa/challenge/verify", json={
            "challenge": second, "code": next_code}).status_code, 401)
        stale = totp_for_step(secret, step - 2)
        self.assertEqual(self.client.post("/api/auth/mfa/challenge/verify", json={
            "challenge": second, "code": stale}).status_code, 401)

    def test_disable_needs_both_factors_and_policy_blocks_personal_bypass(self):
        admin_headers, admin_codes = self._setup("admin")
        student_headers, student_codes = self._setup("student")
        ordinary = self.client.post("/api/auth/mfa/policy", json={
            "password": PASSWORD, "code": student_codes[0], "required": True}, headers=student_headers)
        self.assertEqual(ordinary.status_code, 403)
        policy = self.client.post("/api/auth/mfa/policy", json={
            "password": PASSWORD, "code": admin_codes[0], "required": True}, headers=admin_headers)
        self.assertEqual(policy.status_code, 200, policy.text)
        blocked = self.client.post("/api/auth/mfa/disable", json={
            "password": PASSWORD, "code": student_codes[0]}, headers=student_headers)
        self.assertEqual(blocked.status_code, 403)
        with QaSession(self.engine) as db:
            self.assertIsNotNone(db.get(MfaCredential, self.ids["student"]).enabled_at)
        # Turn policy off with a different unused factor.
        off = self.client.post("/api/auth/mfa/policy", json={
            "password": PASSWORD, "code": admin_codes[1], "required": False}, headers=admin_headers)
        self.assertEqual(off.status_code, 200, off.text)
        self.assertEqual(self.client.post("/api/auth/mfa/disable", json={
            "password": "wrong", "code": student_codes[0]}, headers=student_headers).status_code, 401)
        self.assertEqual(self.client.post("/api/auth/mfa/disable", json={
            "password": PASSWORD, "code": student_codes[0]}, headers=student_headers).status_code, 204)
        self.assertEqual(self.client.get("/api/auth/me", headers=student_headers).status_code, 401)
        self.assertIsNotNone(self._login("student").json().get("access_token"))

    def test_mandatory_policy_enrolls_unenrolled_user_without_portal_token(self):
        admin_headers, admin_codes = self._setup("admin")
        enabled = self.client.post("/api/auth/mfa/policy", json={
            "password": PASSWORD, "code": admin_codes[0], "required": True}, headers=admin_headers)
        self.assertEqual(enabled.status_code, 200, enabled.text)
        login = self._login("staff").json()
        self.assertTrue(login["enrollment_required"])
        self.assertNotIn("access_token", login)
        self.assertEqual(self.client.get("/api/auth/me", headers=self._headers("staff")).status_code, 401)
        begin = self.client.post("/api/auth/mfa/enrollment/start", json={"challenge": login["challenge"]})
        self.assertEqual(begin.status_code, 200, begin.text)
        with QaSession(self.engine) as db:
            secret = decrypt_secret(db.get(MfaCredential, self.ids["staff"]).pending_ciphertext)
        code = totp_for_step(secret, int(datetime.now(timezone.utc).timestamp() // 30))
        completed = self.client.post("/api/auth/mfa/enrollment/confirm", json={
            "challenge": login["challenge"], "code": code})
        self.assertEqual(completed.status_code, 200, completed.text)
        self.assertEqual(len(completed.json()["recovery_codes"]), 8)
        self.assertEqual(self.client.get("/api/auth/me", headers={
            "Authorization": f"Bearer {completed.json()['access_token']}"}).status_code, 200)

    def test_google_exchange_also_requires_mfa(self):
        self._setup("staff")
        raw = "g" * 48
        import hashlib
        with QaSession(self.engine) as db:
            db.add(GoogleLoginTicket(user_id=self.ids["staff"], token_hash=hashlib.sha256(raw.encode()).hexdigest(),
                                     expires_at=datetime.now(timezone.utc) + timedelta(minutes=2)))
            db.commit()
        response = self.client.post("/api/auth/google/exchange", json={"ticket": raw})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["mfa_required"])
        self.assertNotIn("access_token", response.json())

    def test_google_exchange_enforces_selected_portal(self):
        import hashlib
        raw = "s" * 48
        with QaSession(self.engine) as db:
            db.add(GoogleLoginTicket(user_id=self.ids["student"],
                                     token_hash=hashlib.sha256(raw.encode()).hexdigest(),
                                     expires_at=datetime.now(timezone.utc) + timedelta(minutes=2)))
            db.commit()
        response = self.client.post("/api/auth/google/exchange", json={"ticket": raw, "portal": "staff"})
        self.assertEqual(response.status_code, 401, response.text)
        self.assertNotIn("access_token", response.json())

    def test_recovery_regeneration_and_account_isolation(self):
        student_headers, student_codes = self._setup("student")
        staff_headers, staff_codes = self._setup("staff")
        self.assertEqual(self.client.get("/api/auth/mfa/status", headers=student_headers).json()[
            "recovery_codes_remaining"], 8)
        wrong_account = self.client.post("/api/auth/mfa/disable", headers=student_headers, json={
            "password": PASSWORD, "code": staff_codes[0]})
        self.assertEqual(wrong_account.status_code, 401)
        with QaSession(self.engine) as db:
            self.assertIsNotNone(db.get(MfaCredential, self.ids["staff"]).enabled_at)
        regenerated = self.client.post("/api/auth/mfa/recovery/regenerate", headers=student_headers, json={
            "password": PASSWORD, "code": student_codes[0]})
        self.assertEqual(regenerated.status_code, 200, regenerated.text)
        self.assertEqual(len(regenerated.json()["recovery_codes"]), 8)
        self.assertEqual(self.client.post("/api/auth/mfa/recovery/regenerate", headers=student_headers, json={
            "password": PASSWORD, "code": student_codes[1]}).status_code, 401)
        self.assertEqual(self.client.get("/api/auth/mfa/status", headers=student_headers).json()[
            "recovery_codes_remaining"], 8)

    def test_missing_encryption_key_fails_closed(self):
        self._setup("student")
        with patch.object(settings, "mfa_encryption_key", ""):
            response = self._login("student")
        self.assertEqual(response.status_code, 503, response.text)
        self.assertNotIn("access_token", response.json())

    def _start(self, password=PASSWORD, role="student"):
        return self.client.post("/api/auth/mfa/setup/start", json={"password": password},
                                headers=self._headers(role))

    def _pending_rows(self):
        with QaSession(self.engine) as db:
            return db.scalars(select(MfaCredential)).all()

    def test_qr_generation_succeeds_but_mfa_stays_disabled_until_confirmed(self):
        response = self._start()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["qr_code"].startswith("data:image/png;base64,"))
        self.assertEqual(response.headers["cache-control"], "no-store")
        rows = self._pending_rows()
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0].enabled_at)
        self.assertIsNone(rows[0].secret_ciphertext)
        # Login is unchanged: no challenge until setup is confirmed.
        self.assertIn("access_token", self._login("student").json())

    def test_wrong_password_returns_no_qr_and_stores_nothing(self):
        response = self._start(password="Wrong#Password999")
        self.assertEqual(response.status_code, 401, response.text)
        self.assertNotIn("qr_code", response.text)
        self.assertEqual(self._pending_rows(), [])

    def test_missing_or_invalid_key_gives_clear_503_without_secrets_and_stores_nothing(self):
        for bad_key in ("", "not-a-valid-fernet-key", Fernet.generate_key().decode()[:-4]):
            with self.subTest(key_length=len(bad_key)):
                with patch.object(settings, "mfa_encryption_key", bad_key), \
                        self.assertLogs("mfa", level="ERROR") as logs:
                    response = self._start()
                self.assertEqual(response.status_code, 503, response.text)
                self.assertIn("not configured correctly", response.json()["detail"])
                self.assertNotIn("qr_code", response.text)
                self.assertNotIn(PASSWORD, response.text + "".join(logs.output))
                if bad_key:
                    self.assertNotIn(bad_key, response.text + "".join(logs.output))
                self.assertEqual(self._pending_rows(), [])

    def test_existing_mfa_data_needs_its_original_key_and_is_never_rewritten(self):
        headers, _codes = self._setup("student")
        with QaSession(self.engine) as db:
            stored = db.get(MfaCredential, self.ids["student"]).secret_ciphertext
        # A different (valid) key cannot read it: fail closed, no access, data untouched.
        with patch.object(settings, "mfa_encryption_key", Fernet.generate_key().decode()):
            self.assertEqual(self._login("student").json().get("access_token"), None)
            wrong = self.client.post("/api/auth/mfa/challenge/verify", json={
                "challenge": self._login("student").json()["challenge"], "code": "000000"})
            self.assertNotIn("access_token", wrong.text)
        with QaSession(self.engine) as db:
            self.assertEqual(db.get(MfaCredential, self.ids["student"]).secret_ciphertext, stored)
        # The original key still works: a challenge is issued, and the data is readable.
        response = self._login("student")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json().get("challenge"))
        self.assertEqual(self.client.get("/api/auth/mfa/status", headers=headers).json()["enabled"], True)

    def _confirm(self, headers, code):
        return self.client.post("/api/auth/mfa/setup/confirm", json={"code": code}, headers=headers)

    def _enabled(self, role="student"):
        with QaSession(self.engine) as db:
            row = db.get(MfaCredential, self.ids[role])
            return bool(row and row.enabled_at)

    def test_failed_confirmations_never_enable_mfa(self):
        headers = self._headers("student")
        self.assertEqual(self._start().status_code, 200)
        invalid = self._confirm(headers, "000000")
        self.assertEqual(invalid.status_code, 400, invalid.text)
        self.assertIsInstance(invalid.json()["detail"], str)
        for bad in ("123 456", "12345", "abcdef", "1234567", ""):
            response = self._confirm(headers, bad)
            self.assertEqual(response.status_code, 422, (bad, response.text))
            self.assertIsInstance(response.json()["detail"], list)  # the shape the UI must render safely
            self.assertNotIn(bad or "x-never", "".join(str(e.get("input", "")) for e in response.json()["detail"]) or "-")
        self.assertEqual(self.client.post("/api/auth/mfa/setup/confirm", json={}, headers=headers).status_code, 422)
        self.assertFalse(self._enabled())
        with QaSession(self.engine) as db:
            self.assertEqual(db.scalars(select(MfaRecoveryCode)).all(), [])

    def test_confirm_requires_authentication(self):
        self.assertEqual(self.client.post("/api/auth/mfa/setup/confirm", json={"code": "123456"}).status_code, 401)
        self.assertEqual(self.client.post("/api/auth/mfa/setup/confirm", json={"code": "123456"},
                                          headers={"Authorization": "Bearer not-a-token"}).status_code, 401)

    def test_expired_pending_setup_is_rejected_and_stays_disabled(self):
        headers = self._headers("student")
        self.assertEqual(self._start().status_code, 200)
        with QaSession(self.engine) as db:
            row = db.get(MfaCredential, self.ids["student"])
            secret = decrypt_secret(row.pending_ciphertext)
            row.pending_expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
            db.commit()
        code = totp_for_step(secret, int(datetime.now(timezone.utc).timestamp() // 30))
        response = self._confirm(headers, code)
        self.assertEqual(response.status_code, 400, response.text)
        self.assertIn("expired", response.json()["detail"])
        self.assertFalse(self._enabled())

    def test_confirm_database_failure_returns_safe_503_and_leaves_mfa_disabled(self):
        from sqlalchemy.exc import OperationalError
        headers = self._headers("student")
        self.assertEqual(self._start().status_code, 200)
        with QaSession(self.engine) as db:
            secret = decrypt_secret(db.get(MfaCredential, self.ids["student"]).pending_ciphertext)
        code = totp_for_step(secret, int(datetime.now(timezone.utc).timestamp() // 30))
        failure = OperationalError("UPDATE secret-bearing statement", {"code": code}, Exception("db down"))
        with patch("app.api.routes_mfa.replace_recovery_codes", side_effect=failure), \
                self.assertLogs("mfa", level="ERROR") as logs:
            response = self._confirm(headers, code)
        self.assertEqual(response.status_code, 503, response.text)
        self.assertIsInstance(response.json()["detail"], str)
        joined = response.text + "".join(logs.output)
        self.assertNotIn(code, joined)
        self.assertNotIn("secret-bearing", joined)
        self.assertFalse(self._enabled())
        # The same pending setup still succeeds once the database recovers.
        ok = self._confirm(headers, code)
        self.assertEqual(ok.status_code, 200, ok.text)
        self.assertEqual(len(ok.json()["recovery_codes"]), 8)
        self.assertTrue(self._enabled())
        status = self.client.get("/api/auth/mfa/status", headers=headers)
        self.assertNotIn("recovery_codes", status.json())

    def test_database_failure_gives_safe_503_and_rolls_back(self):
        from sqlalchemy.exc import OperationalError
        failure = OperationalError("INSERT secret-bearing statement", {"password": PASSWORD}, Exception("db down"))
        with patch("app.api.routes_mfa._credential", side_effect=failure), \
                self.assertLogs("mfa", level="ERROR") as logs:
            response = self._start()
        self.assertEqual(response.status_code, 503, response.text)
        self.assertIn("No changes were made", response.json()["detail"])
        joined = response.text + "".join(logs.output)
        self.assertNotIn(PASSWORD, joined)
        self.assertNotIn("secret-bearing", joined)
        self.assertEqual(self._pending_rows(), [])

    def test_disabled_account_cannot_finish_existing_challenge(self):
        self._setup("student")
        challenge = self._login("student").json()["challenge"]
        with QaSession(self.engine) as db:
            db.get(User, self.ids["student"]).is_active = False
            db.commit()
        response = self.client.post("/api/auth/mfa/challenge/verify", json={
            "challenge": challenge, "code": "000000"})
        self.assertEqual(response.status_code, 401, response.text)
        self.assertNotIn("access_token", response.json())

    def test_session_creation_failure_rolls_back_mfa_enrollment(self):
        admin_headers, admin_codes = self._setup("admin")
        self.assertEqual(self.client.post("/api/auth/mfa/policy", json={
            "password": PASSWORD, "code": admin_codes[0], "required": True},
            headers=admin_headers).status_code, 200)
        challenge = self._login("staff").json()["challenge"]
        self.assertEqual(self.client.post("/api/auth/mfa/enrollment/start", json={
            "challenge": challenge}).status_code, 200)
        with QaSession(self.engine) as db:
            secret = decrypt_secret(db.get(MfaCredential, self.ids["staff"]).pending_ciphertext)
        code = totp_for_step(secret, int(datetime.now(timezone.utc).timestamp() // 30))
        with patch("app.api.routes_auth.create_access_token", side_effect=RuntimeError("synthetic token failure")):
            with self.assertRaises(RuntimeError):
                self.client.post("/api/auth/mfa/enrollment/confirm", json={
                    "challenge": challenge, "code": code})
        with QaSession(self.engine) as db:
            credential = db.get(MfaCredential, self.ids["staff"])
            self.assertIsNone(credential.enabled_at)
            self.assertIsNotNone(credential.pending_ciphertext)
            self.assertEqual(db.scalar(select(MfaRecoveryCode).where(
                MfaRecoveryCode.user_id == self.ids["staff"])), None)
            self.assertIsNone(db.scalar(select(MfaChallenge).where(
                MfaChallenge.user_id == self.ids["staff"])).used_at)
        retry = self.client.post("/api/auth/mfa/enrollment/confirm", json={
            "challenge": challenge, "code": code})
        self.assertEqual(retry.status_code, 200, retry.text)


if __name__ == "__main__":
    unittest.main()
