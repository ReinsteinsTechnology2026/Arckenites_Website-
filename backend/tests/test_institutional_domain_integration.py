"""Domain provisioning checks against a marked disposable PostgreSQL only."""

import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.institutional_domains import INSTITUTIONAL_DOMAINS
from app.core.security import create_access_token
from app.database import engine
from app.main import app
from app.models.admin_role import AdminRole, SUPER_ADMIN_SLUG
from app.models.program import Program, ProgramEnrollment
from app.models.session import AuthSession
from app.models.user import RoleEnum, User
from app.schemas.admin_students import CredentialEmailOut
from tests.postgres_safety import isolated_postgres_enabled


@unittest.skipUnless(isolated_postgres_enabled(), "Requires marked disposable PostgreSQL")
class InstitutionalDomainPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with Session(bind=engine) as db:
            cls.programs = {p.name: p.id for p in db.scalars(select(Program)).all()}
            role = db.scalar(select(AdminRole).where(AdminRole.slug == SUPER_ADMIN_SLUG))
            assert role is not None
            actor = User(username=f"domain-qa-admin-{uuid4().hex}@example.test",
                         password_hash="unused", full_name="Domain QA Admin", role=RoleEnum.admin,
                         admin_role_id=role.id, is_active=True)
            db.add(actor)
            db.flush()
            cls.actor_id = actor.id
            jti = uuid4()
            cls.token, expires = create_access_token(actor.id, "admin", jti)
            db.add(AuthSession(id=jti, user_id=actor.id, expires_at=expires))
            db.commit()
        cls.client = TestClient(app)
        cls.headers = {"Authorization": f"Bearer {cls.token}"}

    @classmethod
    def tearDownClass(cls):
        with Session(bind=engine) as db:
            db.execute(text("UPDATE users SET is_active=false WHERE id=:id"), {"id": cls.actor_id})
            db.commit()

    def _create(self, program_name, domain_marker="omit"):
        payload = {"full_name": "Domain QA Student", "email": f"domain-qa-{uuid4().hex}@example.test",
                   "temp_password": "DomainQaPassword123", "program_id": self.programs[program_name]}
        if domain_marker != "omit":
            payload["domain"] = domain_marker
        with patch("app.api.routes_admin_students.send_student_credentials",
                   return_value=CredentialEmailOut(credential_email_sent=False, detail="QA email disabled")):
            return self.client.post("/api/admin/students", json=payload, headers=self.headers)

    def test_institutional_requires_domain_and_accepts_exact_nine(self):
        missing = self._create("Institutional Program")
        self.assertEqual(missing.status_code, 400, missing.text)
        self.assertIn("Domain is required", missing.json()["detail"])
        for bad in ("cloud", " Cloud", "Cloud ", "Other", ""):
            with self.subTest(invalid=bad):
                response = self._create("Institutional Program", bad)
                self.assertEqual(response.status_code, 422, response.text)
        for domain in INSTITUTIONAL_DOMAINS:
            with self.subTest(domain=domain):
                response = self._create("Institutional Program", domain)
                self.assertEqual(response.status_code, 201, response.text)
                self.assertEqual(response.json()["domain"], domain)
                with Session(bind=engine) as db:
                    row = db.scalar(select(ProgramEnrollment).where(
                        ProgramEnrollment.student_id == response.json()["id"],
                        ProgramEnrollment.program_id == self.programs["Institutional Program"]))
                    self.assertEqual(row.domain, domain)

    def test_other_programs_reject_domain_and_leave_null(self):
        for name in ("Freshers Job Hack Program", "Certificational Program"):
            with self.subTest(program=name):
                response = self._create(name)
                self.assertEqual(response.status_code, 201, response.text)
                self.assertIsNone(response.json()["domain"])
                with Session(bind=engine) as db:
                    row = db.scalar(select(ProgramEnrollment).where(
                        ProgramEnrollment.student_id == response.json()["id"],
                        ProgramEnrollment.program_id == self.programs[name]))
                    self.assertIsNone(row.domain)
                rejected = self._create(name, "Cloud")
                self.assertEqual(rejected.status_code, 400, rejected.text)

    def test_switching_programs_requires_fresh_domain_and_student_view_is_read_only(self):
        response = self._create("Institutional Program", "Cloud")
        self.assertEqual(response.status_code, 201, response.text)
        student_id = response.json()["id"]
        jti = uuid4()
        student_token, expires = create_access_token(student_id, "student", jti)
        with Session(bind=engine) as db:
            db.add(AuthSession(id=jti, user_id=student_id, expires_at=expires))
            db.commit()
        student_headers = {"Authorization": f"Bearer {student_token}"}
        me = self.client.get("/api/auth/me", headers=student_headers)
        self.assertEqual(me.status_code, 200, me.text)
        self.assertEqual(me.json()["domain"], "Cloud")
        self.assertEqual(self.client.patch(f"/api/admin/students/{student_id}",
                                           json={"domain": "BLM"}, headers=student_headers).status_code, 403)

        freshers_id = self.programs["Freshers Job Hack Program"]
        changed = self.client.patch(f"/api/admin/students/{student_id}",
                                    json={"program_id": freshers_id}, headers=self.headers)
        self.assertEqual(changed.status_code, 200, changed.text)
        self.assertIsNone(changed.json()["domain"])
        self.assertIsNone(self.client.get("/api/auth/me", headers=student_headers).json()["domain"])
        rejected = self.client.patch(f"/api/admin/students/{student_id}",
                                     json={"program_id": self.programs["Institutional Program"],
                                           "full_name": "Should Not Persist"},
                                     headers=self.headers)
        self.assertEqual(rejected.status_code, 400, rejected.text)
        with Session(bind=engine) as db:
            user = db.get(User, student_id)
            self.assertEqual(user.program_id, freshers_id)
            self.assertEqual(user.full_name, "Domain QA Student")
        restored = self.client.patch(f"/api/admin/students/{student_id}",
                                     json={"program_id": self.programs["Institutional Program"],
                                           "domain": "Quantum Computing"}, headers=self.headers)
        self.assertEqual(restored.status_code, 200, restored.text)
        self.assertEqual(restored.json()["domain"], "Quantum Computing")
        self.assertEqual(self.client.get("/api/auth/me", headers=student_headers).json()["domain"],
                         "Quantum Computing")

    def test_database_rejects_unapproved_domain(self):
        response = self._create("Institutional Program", "BLM")
        self.assertEqual(response.status_code, 201, response.text)
        with Session(bind=engine) as db:
            row = db.scalar(select(ProgramEnrollment).where(
                ProgramEnrollment.student_id == response.json()["id"]))
            row.domain = "Unapproved"
            with self.assertRaises(IntegrityError):
                db.commit()
            db.rollback()
            self.assertEqual(db.get(ProgramEnrollment, row.id).domain, "BLM")

    def test_downgrade_refuses_to_drop_assigned_domains(self):
        response = self._create("Institutional Program", "BLM")
        self.assertEqual(response.status_code, 201, response.text)
        path = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "f2c6d8a4e1b3_add_institutional_enrollment_domain.py"
        spec = importlib.util.spec_from_file_location("institutional_domain_migration", path)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        with engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                with self.assertRaisesRegex(RuntimeError, "Cannot drop assigned Institutional domains"):
                    migration.downgrade()
            self.assertEqual(connection.execute(text(
                "SELECT count(*) FROM information_schema.columns WHERE table_name='program_enrollments' "
                "AND column_name='domain'" )).scalar_one(), 1)


if __name__ == "__main__":
    unittest.main()
