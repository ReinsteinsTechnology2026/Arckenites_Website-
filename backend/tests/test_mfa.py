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
