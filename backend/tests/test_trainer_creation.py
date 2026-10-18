"""Trainer email-login provisioning against isolated SQLite and a mocked mail boundary."""
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session

from app.config import settings
from app.core.email import SendResult
from app.core.rate_limit import limiter
from app.core.security import create_access_token, hash_password, verify_password
from app.database import Base, get_db
from app.main import app
from app.models.admin_role import AdminRole
from app.models.audit_log import AuthAuditLog
from app.models.session import AuthSession
from app.models.staff import StaffProfile
from app.models.student import StudentProfile
from app.models.system_settings import SystemSettings
from app.models.user import RoleEnum, User


@compiles(JSONB, "sqlite")
def _jsonb_for_trainer_test(type_, compiler, **kwargs):
    return "JSON"


PASSWORD = "Trainer#Temp123"


class QaSession(Session):
    pass


@event.listens_for(QaSession, "loaded_as_persistent")
def _utc_loaded(session, instance):
    if isinstance(instance, AuthSession) and instance.expires_at.tzinfo is None:
        instance.expires_at = instance.expires_at.replace(tzinfo=timezone.utc)


class TrainerCreationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="arckenites-trainer-qa-")
        self.engine = create_engine(f"sqlite:///{Path(self.temp.name) / 'qa.sqlite'}",
                                    connect_args={"check_same_thread": False})
        @event.listens_for(self.engine, "connect")
        def foreign_keys(connection, record):
            connection.execute("PRAGMA foreign_keys=ON")
        Base.metadata.create_all(self.engine)
        with QaSession(self.engine) as db:
            db.add(AdminRole(id=1, name="Super Admin", slug="super_admin", is_system=True))
            db.add(SystemSettings(id=1))
            db.flush()
            actor = User(username="qa.admin", full_name="QA Admin", role=RoleEnum.admin,
                         admin_role_id=1, password_hash=hash_password("Admin#Test123"), is_active=True)
            db.add(actor)
            db.commit()
            self.actor_id = actor.id
        self.old_overrides = app.dependency_overrides.copy()
        app.dependency_overrides[get_db] = self._db
        self.client = TestClient(app)
        self.limit_patch = patch.object(limiter, "enabled", False)
        self.limit_patch.start()
        self.key_patch = patch.object(settings, "mfa_encryption_key", Fernet.generate_key().decode())
        self.key_patch.start()
        self.send_patch = patch("app.core.trainer_credentials.send_email_detailed",
                                return_value=SendResult(accepted=True))
        self.send = self.send_patch.start()
        self.admin_headers = self._headers(self.actor_id, "admin")

    def tearDown(self):
        self.client.close()
        app.dependency_overrides.clear()
        app.dependency_overrides.update(self.old_overrides)
        self.send_patch.stop()
        self.key_patch.stop()
        self.limit_patch.stop()
        self.engine.dispose()
        self.temp.cleanup()

    def _db(self):
        with QaSession(self.engine, autoflush=False) as db:
            yield db

    def _headers(self, user_id, role):
        jti = uuid4()
        token, expiry = create_access_token(user_id, role, jti)
        with QaSession(self.engine) as db:
            db.add(AuthSession(id=jti, user_id=user_id, expires_at=expiry))
            db.commit()
        return {"Authorization": f"Bearer {token}"}

    def _create(self, **overrides):
        payload = {"full_name": "  Maya Trainer  ", "email": " Maya.Trainer@Example.TEST ",
                   "temp_password": PASSWORD, **overrides}
        return self.client.post("/api/admin/staff", headers=self.admin_headers, json=payload)

    def test_email_login_profile_hash_and_post_commit_delivery(self):
        def observe(email, subject, body, html_body):
            with QaSession(self.engine) as db:
                user = db.scalar(select(User).where(User.username == email))
                self.assertIsNotNone(user, "Email was sent before the account committed")
                self.assertIsNotNone(user.staff_profile)
                self.assertIsNotNone(db.scalar(select(AuthAuditLog).where(
                    AuthAuditLog.target == email)))
            self.assertIn("https://www.arckenites.com/login.html", body)
            self.assertIn(PASSWORD, body)
            self.assertIn(PASSWORD, html_body)
            return SendResult(accepted=True)
        self.send.side_effect = observe
        response = self._create()
        self.assertEqual(response.status_code, 201, response.text)
        data = response.json()
        self.assertEqual(data["username"], "maya.trainer@example.test")
        self.assertEqual(data["email"], data["username"])
        self.assertEqual(data["full_name"], "Maya Trainer")
        self.assertTrue(data["credential_email_sent"])
        self.assertTrue(data["must_change_password"])
        self.assertNotIn(PASSWORD, response.text)
        with QaSession(self.engine) as db:
            user = db.get(User, data["id"])
            self.assertEqual(user.role, RoleEnum.staff)
            self.assertTrue(verify_password(PASSWORD, user.password_hash))
            self.assertNotEqual(user.password_hash, PASSWORD)
            self.assertTrue(user.staff_profile.staff_code.startswith("AK-STF-"))
        self.send.assert_called_once()

    def test_duplicate_email_and_missing_email_are_rejected(self):
        self.assertEqual(self._create().status_code, 201)
        self.send.reset_mock()
        duplicate = self._create(email="MAYA.TRAINER@example.test")
        self.assertEqual(duplicate.status_code, 409, duplicate.text)
        missing = self.client.post("/api/admin/staff", headers=self.admin_headers,
                                   json={"full_name": "Another", "temp_password": PASSWORD})
        self.assertEqual(missing.status_code, 422)
        self.send.assert_not_called()
        with QaSession(self.engine) as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(User).where(
                User.role == RoleEnum.staff)), 1)

    def test_student_profile_email_collision_is_rejected(self):
        with QaSession(self.engine) as db:
            student = User(username="legacy.student", full_name="Student", role=RoleEnum.student,
                           password_hash=hash_password(PASSWORD))
            db.add(student)
            db.flush()
            db.add(StudentProfile(user_id=student.id, email="maya.trainer@example.test"))
            db.commit()
        response = self._create()
        self.assertEqual(response.status_code, 409, response.text)
        self.send.assert_not_called()

    def test_mail_failure_resends_without_creating_another_account(self):
        self.send.return_value = SendResult(accepted=False)
        created = self._create()
        self.assertEqual(created.status_code, 201, created.text)
        self.assertFalse(created.json()["credential_email_sent"])
        trainer_id = created.json()["id"]
        self.send.return_value = SendResult(accepted=True)
        bad = self.client.post(f"/api/admin/staff/{trainer_id}/credentials-email",
                               headers=self.admin_headers, json={"temp_password": "Wrong#Password123"})
        self.assertEqual(bad.status_code, 400)
        retry = self.client.post(f"/api/admin/staff/{trainer_id}/credentials-email",
                                 headers=self.admin_headers, json={"temp_password": PASSWORD})
        self.assertEqual(retry.status_code, 200, retry.text)
        self.assertTrue(retry.json()["credential_email_sent"])
        with QaSession(self.engine) as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(User).where(
                User.role == RoleEnum.staff)), 1)
        self.assertEqual(self.send.call_count, 2)

    def test_failed_commit_sends_no_email_and_rolls_back(self):
        with patch.object(QaSession, "commit", side_effect=RuntimeError("synthetic commit failure")):
            response = self._create()
        self.assertEqual(response.status_code, 500, response.text)
        self.send.assert_not_called()
        with QaSession(self.engine) as db:
            self.assertIsNone(db.scalar(select(User).where(User.username == "maya.trainer@example.test")))

    def test_trainer_login_authorization_mfa_and_revocation(self):
        created = self._create().json()
        login = self.client.post("/api/auth/login", json={
            "username": " MAYA.TRAINER@EXAMPLE.TEST ", "password": PASSWORD})
        self.assertEqual(login.status_code, 200, login.text)
        self.assertIn("access_token", login.json())
        trainer_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        self.assertEqual(self.client.get("/api/staff/me/schedule", headers=trainer_headers).status_code, 200)
        self.assertEqual(self.client.get("/api/admin/staff", headers=trainer_headers).status_code, 403)
        self.assertEqual(self.client.get("/api/students/me/schedule", headers=trainer_headers).status_code, 403)
        with QaSession(self.engine) as db:
            db.get(SystemSettings, 1).mfa_required = True
            db.commit()
        self.assertEqual(self.client.get("/api/staff/me/schedule", headers=trainer_headers).status_code, 401)
        challenged = self.client.post("/api/auth/login", json={
            "username": "maya.trainer@example.test", "password": PASSWORD})
        self.assertEqual(challenged.status_code, 200, challenged.text)
        self.assertTrue(challenged.json()["enrollment_required"])
        self.assertNotIn("access_token", challenged.json())
        with QaSession(self.engine) as db:
            db.get(SystemSettings, 1).mfa_required = False
            db.commit()
        disabled = self.client.patch(f"/api/admin/staff/{created['id']}", headers=self.admin_headers,
                                     json={"is_active": False})
        self.assertEqual(disabled.status_code, 200, disabled.text)
        self.assertEqual(self.client.get("/api/staff/me/schedule", headers=trainer_headers).status_code, 401)
        self.assertEqual(self.client.post("/api/auth/login", json={
            "username": "maya.trainer@example.test", "password": PASSWORD}).status_code, 401)


if __name__ == "__main__":
    unittest.main()
