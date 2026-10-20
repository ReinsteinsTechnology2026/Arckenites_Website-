"""Batch section gates against an isolated SQLite database."""
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session

from app.core.rate_limit import limiter
from app.core.security import create_access_token
from app.database import Base, get_db
from app.main import app
from app.models import (AdminRole, AdminRolePermission, AuthAuditLog, AuthSession, Batch, BatchStatusEnum,
                        BatchEnrollment, InterviewSchedule, JobApplication, Permission, RoleEnum, User)
from app.models.job_application import JobApplicationStatus


@compiles(JSONB, "sqlite")
def _jsonb_sqlite(type_, compiler, **kwargs):
    return "JSON"


class QaSession(Session):
    pass


@event.listens_for(QaSession, "loaded_as_persistent")
def _restore_session_timezone(session, instance):
    if isinstance(instance, AuthSession) and instance.expires_at.tzinfo is None:
        instance.expires_at = instance.expires_at.replace(tzinfo=timezone.utc)


class BatchStudentSectionAccessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="arckenites-sections-")
        self.engine = create_engine(f"sqlite:///{Path(self.temp.name) / 'access.sqlite'}", connect_args={"check_same_thread": False})
        @event.listens_for(self.engine, "connect")
        def _foreign_keys(connection, record):
            connection.execute("PRAGMA foreign_keys=ON")
        Base.metadata.create_all(self.engine)
        with QaSession(self.engine) as db:
            manager = AdminRole(name="Admin", slug="admin", is_system=True)
            viewer = AdminRole(name="Batch Admin", slug="batch_admin")
            db.add_all([manager, viewer])
            for key in ("batches.view", "batches.student_sections_manage"):
                db.add(Permission(key=key, module="Batches", action=key.split(".")[-1]))
            db.flush()
            for role in (manager, viewer):
                db.add(AdminRolePermission(admin_role_id=role.id, permission_key="batches.view"))
            db.add(AdminRolePermission(admin_role_id=manager.id, permission_key="batches.student_sections_manage"))
            users = [
                User(username="section-admin", full_name="Section Admin", role=RoleEnum.admin, admin_role_id=manager.id, password_hash="unused", is_active=True, must_change_password=False),
                User(username="batch-admin", full_name="Batch Admin", role=RoleEnum.admin, admin_role_id=viewer.id, password_hash="unused", is_active=True, must_change_password=False),
                User(username="section-student", full_name="Section Student", role=RoleEnum.student, password_hash="unused", is_active=True, must_change_password=False),
                User(username="other-student", full_name="Other Student", role=RoleEnum.student, password_hash="unused", is_active=True, must_change_password=False),
            ]
            db.add_all(users)
            db.flush()
            self.ids = {u.username: u.id for u in users}
            batches = [Batch(name="First"), Batch(name="Second"), Batch(name="Unrelated")]
            db.add_all(batches)
            db.flush()
            self.batch_ids = [b.id for b in batches]
            for batch in batches[:2]:
                db.add(BatchEnrollment(batch_id=batch.id, student_id=self.ids["section-student"]))
            db.add(BatchEnrollment(batch_id=batches[2].id, student_id=self.ids["other-student"]))
            db.add(InterviewSchedule(student_id=self.ids["section-student"], company_name="Private interview", interview_date=date.today()))
            db.add(JobApplication(title="Published", company="Example", description="Apply", application_url="https://example.org", status=JobApplicationStatus.published, is_enabled=True, published_at=datetime.now(timezone.utc), created_by_id=self.ids["section-admin"]))
            db.add(JobApplication(title="Draft", company="Example", description="Hidden", application_url="https://example.org", status=JobApplicationStatus.draft, is_enabled=False, created_by_id=self.ids["section-admin"]))
            db.commit()
        self.overrides = app.dependency_overrides.copy()
        app.dependency_overrides[get_db] = self._db
        self.client = TestClient(app)
        self.limiter_enabled = limiter.enabled
        limiter.enabled = False

    def tearDown(self):
        self.client.close()
        app.dependency_overrides.clear()
        app.dependency_overrides.update(self.overrides)
        limiter.enabled = self.limiter_enabled
        self.engine.dispose()
        self.temp.cleanup()

    def _db(self):
        with QaSession(self.engine) as db:
            yield db

    def headers(self, name):
        with QaSession(self.engine) as db:
            user = db.get(User, self.ids[name])
            jti = uuid4()
            token, expires = create_access_token(user.id, user.role.value, jti)
            db.add(AuthSession(id=jti, user_id=user.id, expires_at=expires))
            db.commit()
        return {"Authorization": f"Bearer {token}"}

    def toggle(self, batch_index, section, enabled, name="section-admin"):
        return self.client.patch(f"/api/admin/batches/{self.batch_ids[batch_index]}/student-sections/{section}",
                                 headers=self.headers(name), json={"enabled": enabled})

    def test_defaults_independent_access_and_audit(self):
        student = self.headers("section-student")
        other = self.headers("other-student")
        for batch_id in self.batch_ids:
            row = self.client.get(f"/api/admin/batches/{batch_id}", headers=self.headers("section-admin")).json()
            self.assertFalse(row["job_applications_access_enabled"])
            self.assertFalse(row["interview_schedule_access_enabled"])
        with QaSession(self.engine) as db:
            later_batch = Batch(name="Created later")
            db.add(later_batch)
            db.commit()
            db.refresh(later_batch)
            self.assertFalse(later_batch.job_applications_access_enabled)
            self.assertFalse(later_batch.interview_schedule_access_enabled)
        self.assertEqual(self.client.get("/api/student/job-applications/availability", headers=student).json(), {"enabled": False})
        self.assertEqual(self.client.get("/api/students/me/interviews/availability", headers=student).json(), {"enabled": False})
        self.assertEqual(self.client.get("/api/student/job-applications", headers=student).status_code, 403)
        self.assertEqual(self.client.get("/api/students/me/interviews", headers=student).status_code, 403)

        enabled = self.toggle(1, "job_applications", True)
        self.assertEqual(enabled.status_code, 200, enabled.text)
        self.assertTrue(enabled.json()["job_applications_access_enabled"])
        self.assertFalse(enabled.json()["interview_schedule_access_enabled"])
        self.assertEqual(enabled.json()["job_applications_access_updated_by"], "Section Admin")
        jobs = self.client.get("/api/student/job-applications", headers=student)
        self.assertEqual(jobs.status_code, 200, jobs.text)
        self.assertEqual([j["title"] for j in jobs.json()], ["Published"])
        self.assertEqual(self.client.get("/api/student/job-applications", headers=other).status_code, 403)
        self.assertEqual(self.client.get("/api/students/me/interviews", headers=student).status_code, 403)

        self.assertEqual(self.toggle(0, "interview_schedule", True).status_code, 200)
        interviews = self.client.get("/api/students/me/interviews", headers=student)
        self.assertEqual(interviews.status_code, 200, interviews.text)
        self.assertEqual(len(interviews.json()), 1)
        self.assertEqual(self.client.get("/api/students/me/interviews", headers=other).status_code, 403)
        self.assertEqual(self.toggle(2, "interview_schedule", True).status_code, 200)
        self.assertEqual(self.client.get("/api/students/me/interviews", headers=other).json(), [])
        self.assertEqual(self.toggle(1, "job_applications", False).status_code, 200)
        self.assertEqual(self.client.get("/api/student/job-applications", headers=student).status_code, 403)
        self.assertEqual(self.client.get("/api/students/me/interviews", headers=student).status_code, 200)
        with QaSession(self.engine) as db:
            self.assertEqual(db.query(JobApplication).count(), 2)
            self.assertEqual(db.query(InterviewSchedule).count(), 1)
            self.assertEqual(db.query(BatchEnrollment).count(), 3)
            self.assertEqual(db.query(AuthAuditLog).filter(AuthAuditLog.target.like("batch:%")).count(), 4)
        restarted = TestClient(app)
        try:
            self.assertEqual(restarted.get("/api/student/job-applications/availability", headers=student).json(), {"enabled": False})
            self.assertEqual(restarted.get("/api/students/me/interviews/availability", headers=student).json(), {"enabled": True})
        finally:
            restarted.close()

    def test_unauthorized_and_stale_actions(self):
        for name in ("batch-admin", "section-student"):
            self.assertEqual(self.toggle(0, "job_applications", True, name).status_code, 403)
        self.assertEqual(self.toggle(0, "job_applications", "yes").status_code, 422)
        self.assertEqual(self.toggle(0, "unknown", True).status_code, 404)
        with QaSession(self.engine) as db:
            job_id = db.scalar(select(JobApplication.id).where(JobApplication.title == "Published"))
        student = self.headers("section-student")
        self.assertEqual(self.client.get(f"/api/student/job-applications/{job_id}", headers=student).status_code, 403)
        self.assertEqual(self.client.put(f"/api/student/job-applications/{job_id}/reaction", headers=student, json={"kind": "like"}).status_code, 403)
        self.assertEqual(self.client.delete(f"/api/student/job-applications/{job_id}/reaction", headers=student).status_code, 403)
        self.assertEqual(self.toggle(0, "job_applications", True).status_code, 200)
        self.assertEqual(self.client.put(f"/api/student/job-applications/{job_id}/reaction", headers=student, json={"kind": "like"}).status_code, 200)
        self.assertEqual(self.toggle(0, "job_applications", False).status_code, 200)
        self.assertEqual(self.client.put(f"/api/student/job-applications/{job_id}/reaction", headers=student, json={"kind": "love"}).status_code, 403)

    def test_cancelled_batch_is_not_a_valid_enrollment(self):
        self.assertEqual(self.toggle(0, "interview_schedule", True).status_code, 200)
        with QaSession(self.engine) as db:
            batch = db.get(Batch, self.batch_ids[0])
            batch.status = BatchStatusEnum.cancelled
            db.commit()
        student = self.headers("section-student")
        self.assertEqual(self.client.get("/api/students/me/interviews/availability", headers=student).json(), {"enabled": False})
        self.assertEqual(self.client.get("/api/students/me/interviews", headers=student).status_code, 403)

    def test_removing_enrollment_revokes_access_without_removing_records(self):
        self.assertEqual(self.toggle(1, "interview_schedule", True).status_code, 200)
        student = self.headers("section-student")
        self.assertEqual(self.client.get("/api/students/me/interviews", headers=student).status_code, 200)
        with QaSession(self.engine) as db:
            enrollment = db.scalar(select(BatchEnrollment).where(
                BatchEnrollment.batch_id == self.batch_ids[1],
                BatchEnrollment.student_id == self.ids["section-student"],
            ))
            db.delete(enrollment)
            db.commit()
        self.assertEqual(self.client.get("/api/students/me/interviews", headers=student).status_code, 403)
        with QaSession(self.engine) as db:
            self.assertEqual(db.query(InterviewSchedule).count(), 1)


if __name__ == "__main__":
    unittest.main()
