"""Account-scope regressions: disposable SQLite, no configured DB or real mail."""
import importlib.util
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core import google_auth
from app.core.security import hash_password, verify_password
from app.api.routes_community_auth import _email_registered
from app.models.community import CommunityProfile
from app.models.user import User, RoleEnum
from tests import test_student_creation as fixtures
from tests.test_student_creation import ProvisioningTestSession, PASSWORD


class AccountScopeTests(unittest.TestCase):
    setUp = fixtures.StudentCreationTests.setUp
    tearDown = fixtures.StudentCreationTests.tearDown
    _db = fixtures.StudentCreationTests._db
    _headers = fixtures.StudentCreationTests._headers
    _payload = fixtures.StudentCreationTests._payload
    _create = fixtures.StudentCreationTests._create

    def community(self, email="arun@example.com"):
        with ProvisioningTestSession(self.engine) as db:
            user = User(username=email, full_name="Community Original", role=RoleEnum.community,
                        password_hash=hash_password("Community#123"), must_change_password=False)
            db.add(user)
            db.flush()
            db.add(CommunityProfile(user_id=user.id, email=email, mobile_number="9812345678",
                                    mobile_normalized="9812345678", email_verified_at=datetime.now(timezone.utc)))
            db.commit()
            return user.id, user.password_hash

    def login(self, portal, password, email=" ARUN@EXAMPLE.COM "):
        body = {"username": email, "password": password}
        if portal is not None:
            body["portal"] = portal
        return self.client.post("/api/auth/login", json=body)

    def test_student_creation_preserves_community_and_rejects_portal_duplicates(self):
        community_id, original_hash = self.community()
        created = self._create()
        self.assertEqual(created.status_code, 201, created.text)
        self.assertNotEqual(created.json()["id"], community_id)
        self.assertEqual(self._create().status_code, 409)
        with ProvisioningTestSession(self.engine) as db:
            original = db.get(User, community_id)
            self.assertEqual(original.password_hash, original_hash)
            self.assertEqual(original.full_name, "Community Original")
            self.assertEqual(original.community_profile.mobile_normalized, "9812345678")
            self.assertEqual(original.role, RoleEnum.community)

    def test_login_context_sessions_permissions_and_reset_are_isolated(self):
        community_id, original_hash = self.community()
        student_id = self._create().json()["id"]
        for portal, password, expected in [("community", "Community#123", community_id),
                                           ("student", PASSWORD, student_id)]:
            response = self.login(portal, password)
            self.assertEqual(response.status_code, 200, response.text)
            headers = {"Authorization": "Bearer " + response.json()["access_token"]}
            me = self.client.get("/api/auth/me", headers=headers)
            self.assertEqual(me.json()["id"], expected)
            self.assertEqual(me.json()["permissions"], [])
            self.assertEqual(self.client.get("/api/admin/admin-users", headers=headers).status_code, 403)
            if portal == "community":
                community_headers = headers
                self.assertEqual(self.client.get("/api/students/me/batches", headers=headers).status_code, 403)
            else:
                self.assertEqual(self.client.get("/api/community/member/posts", headers=headers).status_code, 403)
        for portal, password in [("community", PASSWORD), ("student", "Community#123"),
                                 ("staff", "Community#123"), (None, PASSWORD), (None, "Community#123")]:
            self.assertEqual(self.login(portal, password).status_code, 401)
        reset = self.client.patch(f"/api/admin/students/{student_id}", headers=self.headers,
                                 json={"reset_temp_password": "NewStudent#123"})
        self.assertEqual(reset.status_code, 200, reset.text)
        self.assertEqual(self.client.get("/api/auth/me", headers=community_headers).json()["id"], community_id)
        with ProvisioningTestSession(self.engine) as db:
            self.assertEqual(db.get(User, community_id).password_hash, original_hash)
            self.assertTrue(verify_password("NewStudent#123", db.get(User, student_id).password_hash))

    def test_trainer_creation_and_staff_login_with_shared_community_email(self):
        community_id, _ = self.community()
        with patch("app.core.trainer_credentials.send_email_detailed") as send:
            send.return_value.accepted = True
            payload = {"full_name": "Trainer", "email": "arun@example.com", "temp_password": PASSWORD}
            created = self.client.post("/api/admin/staff", json=payload, headers=self.headers)
            self.assertEqual(created.status_code, 201, created.text)
            self.assertEqual(self.client.post("/api/admin/staff", json=payload, headers=self.headers).status_code, 409)
        login = self.login("staff", PASSWORD)
        self.assertEqual(login.status_code, 200, login.text)
        self.assertEqual(login.json()["user"]["id"], created.json()["id"])
        self.assertEqual(self.login("community", "Community#123").json()["user"]["id"], community_id)
        self.assertEqual(self._create().status_code, 409)  # Managed scope stays unique.

    def test_admin_and_super_admin_staff_context(self):
        community_id, _ = self.community()
        with ProvisioningTestSession(self.engine) as db:
            admin = db.get(User, self.admin_id)
            admin.username = "arun@example.com"
            admin.password_hash = hash_password(PASSWORD)
            db.commit()
        response = self.login("staff", PASSWORD)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["user"]["id"], self.admin_id)
        self.assertEqual(response.json()["user"]["admin_role"]["slug"], "super_admin")
        self.assertEqual(self.login("community", "Community#123").json()["user"]["id"], community_id)
        self.assertEqual(self.login("student", PASSWORD).status_code, 401)

    def test_database_enforces_normalized_scope_and_legacy_uniqueness(self):
        self.community()
        self.assertEqual(self._create().status_code, 201)
        for role, username in [(RoleEnum.community, " ARUN@EXAMPLE.COM "),
                               (RoleEnum.student, "ARUN@example.com"),
                               (RoleEnum.staff, "arun@example.com"),
                               (RoleEnum.admin, "arun@example.com"),
                               (RoleEnum.community, "admin.existing")]:
            with self.subTest(role=role, username=username), ProvisioningTestSession(self.engine) as db:
                db.add(User(username=username, role=role, full_name="Duplicate", password_hash="unused"))
                with self.assertRaises(IntegrityError):
                    db.commit()

    def test_verified_registration_creates_separate_account_and_token_is_single_use(self):
        from app.core.otp import hash_verification_token
        from app.models.community import CommunityRegistration, CommunityRegistrationStatus
        student_id = self._create().json()["id"]
        token = "test-verification-token-for-community"
        with ProvisioningTestSession(self.engine) as db:
            original_hash = db.get(User, student_id).password_hash
            db.add(CommunityRegistration(full_name="Verified Community", email="arun@example.com",
                mobile_number="9812345678", mobile_normalized="9812345678",
                status=CommunityRegistrationStatus.verified, verified_at=datetime.now(timezone.utc),
                verification_token_hash=hash_verification_token(token),
                verification_token_expires_at=datetime.now(timezone.utc) + timedelta(minutes=10)))
            db.commit()
        payload = {"verification_token": token, "new_password": "Community#123", "confirm_password": "Community#123"}
        response = self.client.post("/api/community/register/set-password", json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.client.post("/api/community/register/set-password", json=payload).status_code, 400)
        with ProvisioningTestSession(self.engine) as db:
            self.assertEqual(db.get(User, student_id).password_hash, original_hash)
            community = db.scalar(select(User).where(User.role == RoleEnum.community))
            self.assertNotEqual(community.id, student_id)
            self.assertIsNotNone(community.community_profile.email_verified_at)

    def test_mfa_challenges_remain_bound_to_selected_identity(self):
        from cryptography.fernet import Fernet
        from app.config import settings
        from app.models.system_settings import SystemSettings
        from app.models.mfa import MfaChallenge
        community_id, _ = self.community()
        student_id = self._create().json()["id"]
        with ProvisioningTestSession(self.engine) as db:
            db.get(SystemSettings, 1).mfa_required = True
            db.commit()
        with patch.object(settings, "mfa_encryption_key", Fernet.generate_key().decode()):
            for portal, password in [("community", "Community#123"), ("student", PASSWORD)]:
                response = self.login(portal, password)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertTrue(response.json()["mfa_required"])
                self.assertNotIn("access_token", response.json())
        with ProvisioningTestSession(self.engine) as db:
            challenges = db.scalars(select(MfaChallenge)).all()
            self.assertCountEqual([c.user_id for c in challenges], [community_id, student_id])

    def test_reverse_registration_checks_and_google_linking_ignore_other_scope(self):
        student_id = self._create().json()["id"]
        with ProvisioningTestSession(self.engine) as db:
            self.assertFalse(_email_registered(db, "arun@example.com"))
        community_id, _ = self.community()
        with ProvisioningTestSession(self.engine) as db:
            self.assertTrue(_email_registered(db, "arun@example.com"))
            matched = google_auth.match_portal_account(db, "test-google-sub", "arun@example.com", False)
            self.assertEqual(matched.id, student_id)
            self.assertNotEqual(matched.id, community_id)


class ScopeMigrationTests(unittest.TestCase):
    def migration(self):
        path = Path(__file__).parents[1] / "alembic/versions/d8e9f0a1b2c3_scope_community_email_login.py"
        spec = importlib.util.spec_from_file_location("scope_migration", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_postgresql_model_ddl_enforces_two_scopes(self):
        from sqlalchemy.schema import CreateIndex
        from sqlalchemy.dialects.postgresql import dialect
        indexes = {index.name: index for index in User.__table__.indexes}
        sql = str(CreateIndex(indexes["uq_users_email_login_normalized"]).compile(dialect=dialect()))
        self.assertIn("CASE WHEN role = 'community'", sql)
        self.assertIn("ELSE 'portal'", sql)
        self.assertIn("lower(trim(username))", sql)
        self.assertFalse(indexes["ix_users_username"].unique)
        self.assertTrue(indexes["uq_users_legacy_username"].unique)

    def test_downgrade_refuses_shared_emails_before_ddl(self):
        migration = self.migration()
        with patch.object(migration, "op") as op:
            op.get_bind.return_value.execute.return_value.scalar_one.return_value = 1
            with self.assertRaisesRegex(RuntimeError, "cross-scope"):
                migration.downgrade()
            op.drop_index.assert_not_called()

    def test_upgrade_replaces_both_global_indexes_without_data_changes(self):
        migration = self.migration()
        with patch.object(migration, "op") as op, patch.object(migration.sa, "inspect") as inspect:
            inspect.return_value.get_unique_constraints.return_value = []
            inspect.return_value.get_indexes.return_value = [{"name": "ix_users_username", "unique": True, "column_names": ["username"]}]
            migration.upgrade()
            self.assertEqual({c.args[0] for c in op.drop_index.call_args_list},
                             {"ix_users_username", "uq_users_email_login_normalized"})
            self.assertEqual(op.create_index.call_count, 3)
            op.get_bind.return_value.execute.assert_not_called()

    def test_upgrade_refuses_unexpected_global_constraint(self):
        migration = self.migration()
        with patch.object(migration, "op") as op, patch.object(migration.sa, "inspect") as inspect:
            inspect.return_value.get_unique_constraints.return_value = [{"column_names": ["username"]}]
            with self.assertRaisesRegex(RuntimeError, "schema drift"):
                migration.upgrade()
            op.drop_index.assert_not_called()
