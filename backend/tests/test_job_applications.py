"""Isolated job announcement HTTP tests; never use the configured database."""
import tempfile
import unittest
from datetime import timezone
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session

from app.core.rate_limit import limiter
from app.core.security import create_access_token
from app.database import Base, get_db
from app.main import app
from app.models import AdminRole, AdminRolePermission, AuthAuditLog, AuthSession, Permission, RoleEnum, SystemSettings, User
from app.models.job_application import JobApplicationReaction


@compiles(JSONB, "sqlite")
def _jsonb_sqlite(type_, compiler, **kwargs):
    return "JSON"


class JobQaSession(Session):
    pass


@event.listens_for(JobQaSession, "loaded_as_persistent")
def _utc_session(session, instance):
    if isinstance(instance, AuthSession) and instance.expires_at.tzinfo is None:
        instance.expires_at = instance.expires_at.replace(tzinfo=timezone.utc)


class JobApplicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="arckenites-job-qa-")
        self.engine = create_engine(f"sqlite:///{Path(self.temp.name) / 'jobs.sqlite'}",
                                    connect_args={"check_same_thread": False})
        @event.listens_for(self.engine, "connect")
        def foreign_keys(connection, record):
            connection.execute("PRAGMA foreign_keys=ON")
        Base.metadata.create_all(self.engine)
        with JobQaSession(self.engine) as db:
            admin_role = AdminRole(name="Placement Admin", slug="placement_admin", is_system=False)
            viewer_role = AdminRole(name="Placement Viewer", slug="placement_viewer", is_system=False)
            db.add_all([admin_role, viewer_role])
            for key, action in (("placement.view", "view"), ("placement.create", "create"), ("placement.edit", "edit")):
                db.add(Permission(key=key, module="Placement", action=action, description=action))
            db.flush()
            for key in ("placement.view", "placement.create", "placement.edit"):
                db.add(AdminRolePermission(admin_role_id=admin_role.id, permission_key=key))
            db.add(AdminRolePermission(admin_role_id=viewer_role.id, permission_key="placement.view"))
            users = [
                User(username="jobs-admin", full_name="Jobs Admin", role=RoleEnum.admin,
                     admin_role_id=admin_role.id, password_hash="unused", is_active=True, must_change_password=False),
                User(username="jobs-student-1", full_name="Student One", role=RoleEnum.student,
                     password_hash="unused", is_active=True, must_change_password=False),
                User(username="jobs-student-2", full_name="Student Two", role=RoleEnum.student,
                     password_hash="unused", is_active=True, must_change_password=False),
                User(username="jobs-trainer", full_name="Trainer", role=RoleEnum.staff,
                     password_hash="unused", is_active=True, must_change_password=False),
                User(username="jobs-community", full_name="Community", role=RoleEnum.community,
                     password_hash="unused", is_active=True, must_change_password=False),
                User(username="jobs-viewer", full_name="Viewer", role=RoleEnum.admin,
                     admin_role_id=viewer_role.id, password_hash="unused", is_active=True, must_change_password=False),
                User(username="jobs-inactive", full_name="Inactive", role=RoleEnum.student,
                     password_hash="unused", is_active=False, must_change_password=False),
            ]
            db.add_all(users + [SystemSettings(id=1)])
            db.commit()
            self.ids = {user.username: user.id for user in users}
        self.original_overrides = app.dependency_overrides.copy()
        app.dependency_overrides[get_db] = self._db
        self.client = TestClient(app)
        self.limiter_enabled = limiter.enabled
        limiter.enabled = False

    def tearDown(self):
        self.client.close()
        app.dependency_overrides.clear()
        app.dependency_overrides.update(self.original_overrides)
        limiter.enabled = self.limiter_enabled
        self.engine.dispose()
        self.temp.cleanup()

    def _db(self):
        with JobQaSession(self.engine) as db:
            yield db

    def headers(self, username):
        with JobQaSession(self.engine) as db:
            user = db.get(User, self.ids[username])
            jti = uuid4()
            token, expires = create_access_token(user.id, user.role.value, jti)
            db.add(AuthSession(id=jti, user_id=user.id, expires_at=expires))
            db.commit()
        return {"Authorization": f"Bearer {token}"}

    def create(self, title="Engineer"):
        result = self.client.post("/api/admin/job-applications", headers=self.headers("jobs-admin"), json={
            "title": title, "company": "Example Co", "description": "Apply for this role.",
            "eligibility": "Open to students", "application_url": "https://example.org/apply", "deadline": None,
        })
        self.assertEqual(result.status_code, 201, result.text)
        return result.json()["id"]

    def status(self, job_id, action):
        return self.client.post(f"/api/admin/job-applications/{job_id}/status",
                                headers=self.headers("jobs-admin"), json={"action": action})

    def test_visibility_status_and_admin_lifecycle(self):
        first = self.create()
        student = self.headers("jobs-student-1")
        admin = self.headers("jobs-admin")
        self.assertEqual(self.client.get("/api/student/job-applications", headers=student).json(), [])
        self.assertEqual(self.client.get(f"/api/student/job-applications/{first}", headers=student).status_code, 404)
        self.assertEqual(self.client.get("/api/admin/job-applications", headers=admin).json()[0]["status"], "draft")
        edited = self.client.patch(f"/api/admin/job-applications/{first}", headers=admin, json={"title": "New Role"})
        self.assertEqual(edited.status_code, 200, edited.text)
        self.assertEqual(self.status(first, "enable").status_code, 409)
        self.assertEqual(self.status(first, "publish").status_code, 200)
        for username in ("jobs-student-1", "jobs-student-2"):
            self.assertEqual(self.client.get("/api/student/job-applications", headers=self.headers(username)).json()[0]["title"], "New Role")
        self.assertEqual(self.status(first, "disable").status_code, 200)
        self.assertEqual(self.client.get("/api/student/job-applications", headers=student).json(), [])
        self.assertEqual(self.client.get(f"/api/student/job-applications/{first}", headers=student).status_code, 404)
        self.assertEqual(self.status(first, "enable").status_code, 200)
        self.assertEqual(self.status(first, "unpublish").status_code, 200)
        self.assertEqual(self.client.get("/api/student/job-applications", headers=student).json(), [])
        self.assertEqual(self.status(first, "publish").status_code, 200)
        self.assertEqual(self.status(first, "archive").status_code, 200)
        self.assertEqual(self.client.get("/api/student/job-applications", headers=student).json(), [])
        self.assertEqual(self.status(first, "publish").status_code, 409)
        with JobQaSession(self.engine) as db:
            events = [row.event_type.value for row in db.scalars(select(AuthAuditLog)).all()]
        self.assertIn("job_application_created", events)
        self.assertIn("job_application_updated", events)
        self.assertIn("job_application_status_changed", events)

    def test_reactions_are_owned_unique_and_preserved_while_disabled(self):
        job = self.create()
        self.assertEqual(self.status(job, "publish").status_code, 200)
        one, two = self.headers("jobs-student-1"), self.headers("jobs-student-2")
        url = f"/api/student/job-applications/{job}/reaction"
        for kind in ("like", "like", "love"):
            result = self.client.put(url, headers=one, json={"kind": kind})
            self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()["reaction_counts"], {"love": 1})
        self.assertEqual(result.json()["my_reaction"], "love")
        self.assertEqual(self.client.put(url, headers=two, json={"kind": "like"}).status_code, 200)
        self.assertEqual(self.client.get(f"/api/student/job-applications/{job}", headers=one).json()["my_reaction"], "love")
        self.assertEqual(self.client.put(url, headers=two,
                         json={"kind": "celebrate", "student_id": self.ids["jobs-student-1"]}).status_code, 422)
        with Session(self.engine) as db:
            db.add(JobApplicationReaction(job_id=job, student_id=self.ids["jobs-student-1"], kind="like"))
            with self.assertRaises(IntegrityError):
                db.commit()
            db.rollback()
        self.assertEqual(self.client.delete(url, headers=two).json()["reaction_counts"], {"love": 1})
        with JobQaSession(self.engine) as db:
            self.assertEqual(len(db.scalars(select(JobApplicationReaction)).all()), 1)
        self.assertEqual(self.status(job, "disable").status_code, 200)
        self.assertEqual(self.client.put(url, headers=one, json={"kind": "like"}).status_code, 404)
        self.assertEqual(self.client.delete(url, headers=one).status_code, 404)
        self.assertEqual(self.status(job, "enable").json()["reaction_counts"], {"love": 1})
        self.assertEqual(self.client.delete(url, headers=one).json()["reaction_counts"], {})
        self.assertEqual(self.status(job, "archive").status_code, 200)
        self.assertEqual(self.client.put(url, headers=one, json={"kind": "like"}).status_code, 404)

    def test_authorization_urls_and_no_comment_endpoint(self):
        admin = self.headers("jobs-admin")
        student = self.headers("jobs-student-1")
        trainer = self.headers("jobs-trainer")
        community = self.headers("jobs-community")
        payload = {"title": "Role", "company": "Co", "description": "Details", "application_url": "javascript:alert(1)"}
        self.assertEqual(self.client.post("/api/admin/job-applications", headers=admin, json=payload).status_code, 422)
        payload["application_url"] = "https://user:password@example.org/apply"
        self.assertEqual(self.client.post("/api/admin/job-applications", headers=admin, json=payload).status_code, 422)
        payload["application_url"] = "https://example.org\\@evil.test/apply"
        self.assertEqual(self.client.post("/api/admin/job-applications", headers=admin, json=payload).status_code, 422)
        payload["application_url"] = "http://127.0.0.1:8000/apply"
        self.assertEqual(self.client.post("/api/admin/job-applications", headers=admin, json=payload).status_code, 422)
        for headers in (student, trainer, community):
            self.assertEqual(self.client.post("/api/admin/job-applications", headers=headers, json=payload).status_code, 403)
            self.assertEqual(self.client.get("/api/admin/job-applications", headers=headers).status_code, 403)
        job = self.create()
        viewer = self.headers("jobs-viewer")
        self.assertEqual(self.client.get("/api/admin/job-applications", headers=viewer).status_code, 200)
        self.assertEqual(self.client.post("/api/admin/job-applications", headers=viewer, json=payload).status_code, 403)
        self.assertEqual(self.client.patch(f"/api/admin/job-applications/{job}", headers=viewer, json={"title": "Changed"}).status_code, 403)
        self.assertEqual(self.client.post(f"/api/admin/job-applications/{job}/status", headers=viewer, json={"action": "publish"}).status_code, 403)
        self.assertEqual(self.client.post(f"/api/student/job-applications/{job}/comments", headers=student, json={"text": "x"}).status_code, 404)
        self.assertEqual(self.client.get("/api/student/job-applications", headers=trainer).status_code, 403)
        self.assertEqual(self.client.get("/api/student/job-applications", headers=community).status_code, 403)
        self.assertEqual(self.client.get("/api/student/job-applications", headers=self.headers("jobs-inactive")).status_code, 401)


if __name__ == "__main__":
    unittest.main()
