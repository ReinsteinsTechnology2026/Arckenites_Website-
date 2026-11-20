"""Trainer recording approval for class occurrences (isolated SQLite, temp storage).

Covers: the one-time prompt, trainer-only approval, student denial, idempotent
and duplicate approvals, decline semantics, reschedule/ad-hoc occurrences,
infrastructure gating, audit rows, and that the migration matches the models.
"""
import importlib.util
import tempfile
import unittest
from datetime import date, time, timezone
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, inspect, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session

from app.config import settings
from app.core import class_runs, job_queue
from app.core.recording_storage import REPO_ROOT
from app.core.rate_limit import limiter
from app.core.security import create_access_token
from app.database import Base, get_db
from app.main import app
from app.models import (AdminRole, AdminRolePermission, AuthAuditLog, AuthSession, Batch, BatchEnrollment,
                        ClassSession, Permission, RoleEnum, User)
from app.models.admin_role import SUPER_ADMIN_SLUG
from app.models.audit_log import AuthEventType
from app.models.class_run import (APPROVAL_APPROVED, APPROVAL_DECLINED, APPROVAL_PENDING, REC_APPROVED,
                                  REC_AWAITING_APPROVAL, REC_DECLINED, REC_NOT_RECORDED, RUN_CANCELLED,
                                  RUN_ENDED, RUN_ROOM_OPEN, BackgroundJob, ClassSessionRun, RecordingEvent)


@compiles(JSONB, "sqlite")
def _jsonb_sqlite(type_, compiler, **kwargs):
    return "JSON"


class QaSession(Session):
    pass


@event.listens_for(QaSession, "loaded_as_persistent")
def _restore_session_timezone(session, instance):
    if isinstance(instance, AuthSession) and instance.expires_at.tzinfo is None:
        instance.expires_at = instance.expires_at.replace(tzinfo=timezone.utc)


def _user(username, role, **extra):
    return User(username=username, full_name=username.title(), role=role, password_hash="unused",
                is_active=True, must_change_password=False, **extra)


class ApprovalApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="arckenites-runs-", ignore_cleanup_errors=True)
        self.storage = Path(self.temp.name) / "recordings"
        self.storage.mkdir()
        self.engine = create_engine(f"sqlite:///{Path(self.temp.name) / 'runs.sqlite'}",
                                    connect_args={"check_same_thread": False})

        @event.listens_for(self.engine, "connect")
        def _fk(connection, record):
            connection.execute("PRAGMA foreign_keys=ON")

        Base.metadata.create_all(self.engine)
        with QaSession(self.engine) as db:
            viewer = AdminRole(name="Recording Viewer", slug="recording_viewer")
            plain = AdminRole(name="Plain Admin", slug="plain_admin")
            superadmin = AdminRole(name="Super Admin", slug=SUPER_ADMIN_SLUG, is_system=True)
            db.add_all([viewer, plain, superadmin])
            db.add(Permission(key="recordings.view", module="Recordings", action="view"))
            db.flush()
            db.add(AdminRolePermission(admin_role_id=viewer.id, permission_key="recordings.view"))
            users = [
                _user("trainer", RoleEnum.staff), _user("other-trainer", RoleEnum.staff),
                _user("student", RoleEnum.student), _user("outsider", RoleEnum.student),
                _user("viewer-admin", RoleEnum.admin, admin_role_id=viewer.id),
                _user("super-admin", RoleEnum.admin, admin_role_id=superadmin.id),
                _user("plain-admin", RoleEnum.admin, admin_role_id=plain.id),
            ]
            db.add_all(users)
            db.flush()
            self.ids = {u.username: u.id for u in users}
            batch = Batch(name="Batch A", trainer_id=self.ids["trainer"])
            other = Batch(name="Batch B", trainer_id=self.ids["other-trainer"])
            db.add_all([batch, other])
            db.flush()
            self.batch_id, self.other_batch_id = batch.id, other.id
            db.add(BatchEnrollment(batch_id=batch.id, student_id=self.ids["student"]))
            db.add(BatchEnrollment(batch_id=other.id, student_id=self.ids["outsider"]))
            db.flush()
            self.run_id = self._make_run(db, batch, "run-a")
            self.other_run_id = self._make_run(db, other, "run-b")
            db.commit()

        self.overrides = app.dependency_overrides.copy()
        app.dependency_overrides[get_db] = self._db
        self.client = TestClient(app)
        self.limiter_enabled = limiter.enabled
        limiter.enabled = False
        self.patches = [
            patch.object(settings, "recording_enabled", True),
            patch.object(settings, "livekit_url", "wss://media.example.test"),
            patch.object(settings, "livekit_api_key", "APItestkey"),
            patch.object(settings, "livekit_api_secret", "test-livekit-secret-" + "x" * 32),
            patch.object(settings, "recording_staging_dir", str(self.storage)),
            patch.object(settings, "recording_min_free_gb", 0.0),
        ]
        for p in self.patches:
            p.start()
        self.consent = settings.recording_consent_version

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.client.close()
        app.dependency_overrides.clear()
        app.dependency_overrides.update(self.overrides)
        limiter.enabled = self.limiter_enabled
        self.engine.dispose()
        self.temp.cleanup()

    @staticmethod
    def _make_run(db, batch, key):
        now = class_runs.utcnow()
        run = ClassSessionRun(
            occurrence_key=key, kind="SCHEDULED", batch_id=batch.id, trainer_id=batch.trainer_id,
            scheduled_start_at=now, scheduled_end_at=now, effective_end_at=now, state=RUN_ROOM_OPEN,
            approval_status=APPROVAL_PENDING, recording_state=REC_AWAITING_APPROVAL,
        )
        db.add(run)
        db.flush()
        return run.id

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

    def approve(self, name="trainer", run_id=None, decision="approve", **body):
        body.setdefault("consent_version", self.consent)
        return self.client.post(f"/api/class-runs/{run_id or self.run_id}/recording/approval",
                                headers=self.headers(name), json={"decision": decision, **body})

    def count(self, model, *where):
        with QaSession(self.engine) as db:
            return db.scalar(select(func.count()).select_from(model).where(*where))

    def run_row(self, run_id=None):
        with QaSession(self.engine) as db:
            row = db.get(ClassSessionRun, run_id or self.run_id)
            db.expunge(row)
            return row

    # -- authorization ------------------------------------------------------
    def test_student_cannot_approve_or_decline_or_claim_prompt(self):
        self.assertEqual(self.approve("student").status_code, 403)
        self.assertEqual(self.approve("student", decision="decline").status_code, 403)
        r = self.client.post(f"/api/class-runs/{self.run_id}/recording/prompt", headers=self.headers("student"))
        self.assertEqual(r.status_code, 403)
        row = self.run_row()
        self.assertEqual((row.approval_status, row.recording_state), (APPROVAL_PENDING, REC_AWAITING_APPROVAL))
        self.assertEqual(self.count(BackgroundJob), 0)

    def test_outsiders_and_other_batch_trainers_cannot_even_see_the_class(self):
        self.assertEqual(self.approve("outsider").status_code, 404)
        self.assertEqual(self.approve("other-trainer").status_code, 404)
        self.assertEqual(self.client.get(f"/api/class-runs/{self.run_id}/recording",
                                         headers=self.headers("outsider")).status_code, 404)
        self.assertEqual(self.count(BackgroundJob), 0)

    def test_admin_without_permission_is_refused_and_unauthenticated_is_401(self):
        self.assertEqual(self.approve("plain-admin").status_code, 404)
        r = self.client.post(f"/api/class-runs/{self.run_id}/recording/approval",
                             json={"decision": "approve", "consent_version": self.consent})
        self.assertEqual(r.status_code, 401)

    def test_there_is_no_admin_override_not_even_for_a_super_admin(self):
        # An admin may be allowed to SEE a class's recording status, but nobody
        # except the class trainer can approve, decline, end or extend.
        for name in ("viewer-admin", "super-admin"):
            with self.subTest(name):
                self.assertEqual(self.approve(name).status_code, 403)
                self.assertEqual(self.approve(name, decision="decline").status_code, 403)
                for action in ("end", "trainer-leave"):
                    self.assertEqual(self.client.post(f"/api/class-runs/{self.run_id}/{action}",
                                                      headers=self.headers(name)).status_code, 403)
                view = self.client.get(f"/api/class-runs/{self.run_id}/recording", headers=self.headers(name)).json()
                self.assertFalse(view["can_control"])
        row = self.run_row()
        self.assertEqual((row.approval_status, row.recording_state), (APPROVAL_PENDING, REC_AWAITING_APPROVAL))
        self.assertEqual(self.count(BackgroundJob), 0)

    # -- the one-time prompt ----------------------------------------------
    def test_prompt_is_shown_once_per_occurrence_regardless_of_refresh_or_second_tab(self):
        h = self.headers("trainer")
        first = self.client.post(f"/api/class-runs/{self.run_id}/recording/prompt", headers=h).json()
        again = self.client.post(f"/api/class-runs/{self.run_id}/recording/prompt", headers=self.headers("trainer")).json()
        self.assertTrue(first["show"])
        self.assertFalse(again["show"])
        self.assertEqual(self.count(RecordingEvent, RecordingEvent.event == "approval_requested"), 1)
        self.assertEqual(self.count(AuthAuditLog, AuthAuditLog.event_type == AuthEventType.recording_approval_requested), 1)

    def test_dismissing_the_prompt_leaves_approval_pending_and_never_starts_recording(self):
        self.client.post(f"/api/class-runs/{self.run_id}/recording/prompt", headers=self.headers("trainer"))
        status = self.client.get(f"/api/class-runs/{self.run_id}/recording", headers=self.headers("trainer")).json()
        self.assertEqual((status["approval_status"], status["recording_state"], status["is_recording"]),
                         (APPROVAL_PENDING, REC_AWAITING_APPROVAL, False))
        self.assertTrue(status["can_control"])  # the persistent Start Recording control stays available
        self.assertEqual(self.count(BackgroundJob), 0)

    # -- approval -----------------------------------------------------------
    def test_trainer_approval_persists_and_enqueues_exactly_one_start_job(self):
        r = self.approve()
        self.assertEqual(r.status_code, 200, r.text)
        row = self.run_row()
        self.assertEqual((row.approval_status, row.recording_state, row.approval_source),
                         (APPROVAL_APPROVED, REC_APPROVED, "TRAINER"))
        self.assertEqual(row.approval_decided_by, self.ids["trainer"])
        self.assertIsNotNone(row.approval_decided_at)
        self.assertEqual(row.consent_text_version, self.consent)
        with QaSession(self.engine) as db:
            jobs = db.scalars(select(BackgroundJob)).all()
        self.assertEqual([(j.kind, j.dedupe_key) for j in jobs], [("recording.start", f"recording.start:{self.run_id}")])
        # approval is APPROVED, not RECORDING: nothing claims recording has begun yet
        self.assertFalse(r.json()["is_recording"])

    def test_duplicate_and_repeated_approvals_do_not_duplicate_jobs_events_or_audit(self):
        for _ in range(3):
            self.assertEqual(self.approve().status_code, 200)
        self.assertEqual(self.count(BackgroundJob), 1)
        self.assertEqual(self.count(RecordingEvent, RecordingEvent.event == "approval_granted"), 1)
        self.assertEqual(self.count(AuthAuditLog, AuthAuditLog.event_type == AuthEventType.recording_approval_granted), 1)

    def test_enqueue_is_idempotent_at_the_database_level(self):
        with QaSession(self.engine) as db:
            self.assertTrue(job_queue.enqueue(db, "recording.start", "k", {}))
            db.commit()
            self.assertFalse(job_queue.enqueue(db, "recording.start", "k", {}))
            db.commit()
            self.assertEqual(db.scalar(select(func.count()).select_from(BackgroundJob)), 1)

    def test_stale_consent_wording_is_rejected_and_persists_nothing(self):
        r = self.approve(consent_version="old-wording")
        self.assertEqual(r.status_code, 409)
        self.assertEqual(self.run_row().approval_status, APPROVAL_PENDING)
        self.assertEqual(self.count(BackgroundJob), 0)

    def test_missing_consent_version_is_rejected(self):
        r = self.client.post(f"/api/class-runs/{self.run_id}/recording/approval", headers=self.headers("trainer"),
                             json={"decision": "approve"})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(self.count(BackgroundJob), 0)

    def test_unavailable_recording_infrastructure_is_reported_and_nothing_is_persisted(self):
        cases = {
            "recording_enabled": False,
            "livekit_url": "",
            "livekit_api_key": "",
            "livekit_api_secret": "",
            "recording_staging_dir": "",
            "recording_staging_dir_missing": str(self.storage / "does-not-exist"),
        }
        for name, value in cases.items():
            with self.subTest(name):
                attr = "recording_staging_dir" if name.startswith("recording_staging_dir") else name
                with patch.object(settings, attr, value):
                    r = self.approve()
                self.assertEqual(r.status_code, 503, r.text)
                self.assertEqual(self.run_row().approval_status, APPROVAL_PENDING)
                self.assertEqual(self.count(BackgroundJob), 0)

    def test_full_disk_blocks_approval(self):
        with patch.object(settings, "recording_min_free_gb", 10 ** 9):
            r = self.approve()
        self.assertEqual(r.status_code, 503)
        self.assertIn("nearly full", r.json()["detail"])
        self.assertEqual(self.count(BackgroundJob), 0)

    def test_staging_inside_the_repository_is_refused(self):
        with patch.object(settings, "recording_staging_dir", str(REPO_ROOT)):
            self.assertIn("misconfigured", class_runs.recording_unavailable_reason())

    # -- decline ------------------------------------------------------------
    def test_decline_never_starts_recording_and_is_idempotent(self):
        r = self.approve(decision="decline")
        self.assertEqual(r.status_code, 200, r.text)
        row = self.run_row()
        self.assertEqual((row.approval_status, row.recording_state), (APPROVAL_DECLINED, REC_DECLINED))
        self.assertFalse(r.json()["is_recording"])
        self.assertEqual(self.count(BackgroundJob), 0)
        self.assertEqual(self.approve(decision="decline").status_code, 200)
        self.assertEqual(self.count(AuthAuditLog, AuthAuditLog.event_type == AuthEventType.recording_approval_declined), 1)

    def test_a_declined_class_can_later_be_explicitly_approved_but_never_silently(self):
        self.approve(decision="decline")
        self.assertEqual(self.count(BackgroundJob), 0)  # decline alone never starts anything
        self.assertEqual(self.approve().status_code, 200)  # explicit, audited re-approval
        self.assertEqual(self.count(BackgroundJob), 1)
        self.assertEqual(self.count(AuthAuditLog, AuthAuditLog.event_type == AuthEventType.recording_approval_granted), 1)

    def test_declining_after_approval_is_refused(self):
        self.approve()
        r = self.approve(decision="decline")
        self.assertEqual(r.status_code, 409)
        self.assertEqual(self.run_row().approval_status, APPROVAL_APPROVED)

    # -- lifecycle ----------------------------------------------------------
    def test_ended_or_cancelled_classes_cannot_be_approved(self):
        for state in (RUN_ENDED, RUN_CANCELLED):
            with self.subTest(state):
                with QaSession(self.engine) as db:
                    db.get(ClassSessionRun, self.run_id).state = state
                    db.commit()
                self.assertEqual(self.approve().status_code, 409)
                self.assertEqual(self.count(BackgroundJob), 0)

    def test_a_class_that_has_not_opened_yet_cannot_be_approved(self):
        with QaSession(self.engine) as db:
            db.get(ClassSessionRun, self.run_id).state = "SCHEDULED"
            db.commit()
        self.assertEqual(self.approve().status_code, 409)

    def test_students_see_only_a_recording_flag_not_the_approval_machinery_controls(self):
        self.approve()
        view = self.client.get(f"/api/class-runs/{self.run_id}/recording", headers=self.headers("student")).json()
        self.assertFalse(view["can_control"])
        self.assertFalse(view["is_recording"])  # APPROVED is not RECORDING

    def test_trainer_reassignment_moves_approval_authority(self):
        with QaSession(self.engine) as db:
            db.get(Batch, self.batch_id).trainer_id = self.ids["other-trainer"]
            db.commit()
        self.assertEqual(self.approve("trainer").status_code, 404)
        self.assertEqual(self.approve("other-trainer").status_code, 200)


class OccurrenceTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
        Base.metadata.create_all(self.engine)
        self.db = QaSession(self.engine)
        self.trainer = _user("trainer", RoleEnum.staff)
        self.db.add(self.trainer)
        self.db.flush()
        self.batch = Batch(name="B", trainer_id=self.trainer.id)
        self.db.add(self.batch)
        self.db.flush()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def session(self, start=time(10), end=time(12), day=date(2026, 11, 2)):
        s = ClassSession(batch_id=self.batch.id, title="Class", session_date=day, start_time=start, end_time=end)
        self.db.add(s)
        self.db.flush()
        return s

    def test_session_window_uses_the_business_timezone(self):
        s = self.session()
        start, end = class_runs.session_window(s)
        # 10:00 Asia/Kolkata == 04:30 UTC
        self.assertEqual((start.hour, start.minute, end.hour), (4, 30, 6))

    def test_overnight_session_ends_the_next_day(self):
        s = self.session(start=time(23), end=time(1))
        start, end = class_runs.session_window(s)
        self.assertEqual((end - start).total_seconds(), 2 * 3600)

    def test_session_without_times_is_ineligible(self):
        s = self.session(start=None, end=None)
        self.assertIsNone(class_runs.session_window(s))
        with self.assertRaises(Exception) as ctx:
            class_runs.get_or_create_session_run(self.db, s, self.batch)
        self.assertEqual(ctx.exception.status_code, 409)

    def test_get_or_create_is_idempotent_for_the_same_occurrence(self):
        s = self.session()
        a = class_runs.get_or_create_session_run(self.db, s, self.batch)
        b = class_runs.get_or_create_session_run(self.db, s, self.batch)
        self.assertEqual(a.id, b.id)
        self.assertEqual(self.db.scalar(select(func.count()).select_from(ClassSessionRun)), 1)
        self.assertEqual((a.approval_status, a.recording_state), (APPROVAL_PENDING, REC_AWAITING_APPROVAL))

    def test_rescheduling_creates_a_new_occurrence_that_needs_a_new_approval(self):
        s = self.session()
        old = class_runs.get_or_create_session_run(self.db, s, self.batch)
        old.state = RUN_ROOM_OPEN
        old.approval_status, old.recording_state = APPROVAL_APPROVED, REC_APPROVED  # approved for the OLD time
        self.db.commit()

        s.start_time, s.end_time = time(15), time(17)  # reschedule
        self.db.commit()
        new = class_runs.get_or_create_session_run(self.db, s, self.batch)

        self.assertNotEqual(new.id, old.id)
        self.assertEqual((new.approval_status, new.recording_state), (APPROVAL_PENDING, REC_AWAITING_APPROVAL))
        self.db.refresh(old)
        self.assertEqual((old.state, old.end_reason, old.recording_state), (RUN_CANCELLED, "RESCHEDULED", REC_NOT_RECORDED))

    def test_a_started_occurrence_is_not_cancelled_by_a_later_edit(self):
        s = self.session()
        old = class_runs.get_or_create_session_run(self.db, s, self.batch)
        old.state, old.actual_start_at = "LIVE", class_runs.utcnow()
        self.db.commit()
        s.start_time = time(15)
        class_runs.get_or_create_session_run(self.db, s, self.batch)
        self.db.refresh(old)
        self.assertEqual(old.state, "LIVE")

    def test_adhoc_class_uses_the_same_workflow_and_reuses_the_open_occurrence(self):
        a = class_runs.get_or_create_adhoc_run(self.db, self.batch)
        b = class_runs.get_or_create_adhoc_run(self.db, self.batch)
        self.assertEqual(a.id, b.id)
        self.assertEqual((a.kind, a.state, a.approval_status, a.class_session_id),
                         ("AD_HOC", RUN_ROOM_OPEN, APPROVAL_PENDING, None))
        a.state = RUN_ENDED
        self.db.commit()
        c = class_runs.get_or_create_adhoc_run(self.db, self.batch)
        self.assertNotEqual(c.id, a.id)
        self.assertEqual(c.approval_status, APPROVAL_PENDING)  # a later class needs its own approval

    def test_database_rejects_a_second_open_adhoc_occurrence_for_a_batch(self):
        from sqlalchemy.exc import IntegrityError
        class_runs.get_or_create_adhoc_run(self.db, self.batch)
        now = class_runs.utcnow()
        self.db.add(ClassSessionRun(
            occurrence_key="adhoc:dup", kind="AD_HOC", batch_id=self.batch.id, scheduled_start_at=now,
            scheduled_end_at=now, effective_end_at=now, state=RUN_ROOM_OPEN, approval_status=APPROVAL_PENDING,
            recording_state=REC_AWAITING_APPROVAL))
        with self.assertRaises(IntegrityError):
            self.db.flush()
        self.db.rollback()

    def test_database_rejects_invalid_states(self):
        from sqlalchemy.exc import IntegrityError
        now = class_runs.utcnow()
        self.db.add(ClassSessionRun(
            occurrence_key="bad", kind="SCHEDULED", batch_id=self.batch.id, scheduled_start_at=now,
            scheduled_end_at=now, effective_end_at=now, state="BOGUS", approval_status=APPROVAL_PENDING,
            recording_state=REC_AWAITING_APPROVAL))
        with self.assertRaises(IntegrityError):
            self.db.flush()
        self.db.rollback()


class MigrationMatchesModelsTests(unittest.TestCase):
    """Runs the real migration against a fresh SQLite database and compares
    the resulting tables with the ORM models, so the two cannot drift."""

    TABLES = ("class_session_runs", "class_recordings", "recording_events", "background_jobs")

    def _load_migration(self):
        path = next(Path(__file__).parents[1].glob("alembic/versions/d4e8a1b7c356_*.py"))
        spec = importlib.util.spec_from_file_location("class_runs_migration", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def _load_later(self, pattern):
        path = next(Path(__file__).parents[1].glob(f"alembic/versions/{pattern}"))
        spec = importlib.util.spec_from_file_location(path.stem, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_upgrade_creates_the_model_schema_and_downgrade_removes_it(self):
        engine = create_engine("sqlite://")
        # Parent tables the new tables point at.
        Base.metadata.create_all(engine, tables=[Base.metadata.tables[t] for t in
                                                 ("users", "batches", "class_sessions", "meetings", "admin_roles")
                                                 if t in Base.metadata.tables])
        migration = self._load_migration()
        # Later additive revisions that change these tables, applied in order after the original.
        later = [self._load_later("f1a7c3d9b254_*.py")]
        with engine.begin() as conn:
            with Operations.context(MigrationContext.configure(conn)):
                migration.upgrade()
                for extra in later:
                    extra.upgrade()
            inspector = inspect(conn)
            for table in self.TABLES:
                with self.subTest(table):
                    migrated = {c["name"]: c for c in inspector.get_columns(table)}
                    modelled = Base.metadata.tables[table].columns
                    self.assertEqual(set(migrated), {c.name for c in modelled})
                    for column in modelled:
                        self.assertEqual(migrated[column.name]["nullable"], column.nullable,
                                         f"{table}.{column.name} nullability differs")
            indexes = {i["name"] for t in self.TABLES for i in inspector.get_indexes(t)}
            for expected in ("uq_class_session_runs_open_ad_hoc", "uq_class_recordings_active_per_run",
                             "ix_background_jobs_state_run_at"):
                self.assertIn(expected, indexes)
            with Operations.context(MigrationContext.configure(conn)):
                for extra in reversed(later):
                    extra.downgrade()
                migration.downgrade()
            self.assertFalse(set(self.TABLES) & set(inspect(conn).get_table_names()))

    def test_migration_declares_every_audit_event_the_enum_now_has(self):
        migration = self._load_migration()
        new_enum_values = {e.value for e in AuthEventType if e.value.startswith("recording_") or e.value == "class_run_cancelled"}
        self.assertEqual(set(migration.NEW_AUDIT_EVENTS), new_enum_values)


if __name__ == "__main__":
    unittest.main()


class ClassVideosMigrationTests(unittest.TestCase):
    """Runs both recording migrations in order against the ORIGINAL class_videos
    table, checks the result matches the models, that existing rows survive as
    links, and that the downgrade restores the original shape."""

    def _load(self, pattern):
        path = next(Path(__file__).parents[1].glob(pattern))
        spec = importlib.util.spec_from_file_location(path.stem, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_upgrade_matches_the_models_preserves_links_and_downgrades_cleanly(self):
        from sqlalchemy import text
        engine = create_engine("sqlite://")
        parents = [Base.metadata.tables[t] for t in ("users", "programs", "batches", "class_sessions", "meetings", "admin_roles")]
        Base.metadata.create_all(engine, tables=parents)
        first = self._load("alembic/versions/d4e8a1b7c356_*.py")
        second = self._load("alembic/versions/e5f9b2c8d471_*.py")
        with engine.begin() as conn:
            conn.execute(text("CREATE TABLE class_videos (id INTEGER PRIMARY KEY, batch_id INTEGER NOT NULL REFERENCES batches(id),"
                              " title VARCHAR(200) NOT NULL, video_url VARCHAR(500) NOT NULL, description TEXT,"
                              " created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP)"))
            conn.execute(text("INSERT INTO batches (id, name, batch_type, status) VALUES (1, 'B', 'online', 'active')"))
            conn.execute(text("INSERT INTO class_videos (batch_id, title, video_url) VALUES (1, 'old link', 'https://example.com/a')"))
            with Operations.context(MigrationContext.configure(conn)):
                first.upgrade()
                second.upgrade()
            inspector = inspect(conn)
            migrated = {c["name"]: c for c in inspector.get_columns("class_videos")}
            modelled = Base.metadata.tables["class_videos"].columns
            self.assertEqual(set(migrated), {c.name for c in modelled})
            for column in modelled:
                if not column.primary_key:  # SQLite reports INTEGER PRIMARY KEY as nullable; a PK never is
                    self.assertEqual(migrated[column.name]["nullable"], column.nullable, f"class_videos.{column.name}")
            row = conn.execute(text("SELECT title, video_url, source, recording_id FROM class_videos")).one()
            self.assertEqual(tuple(row), ("old link", "https://example.com/a", "LINK", None))  # existing links untouched
            with Operations.context(MigrationContext.configure(conn)):
                second.downgrade()
            restored = {c["name"]: c for c in inspect(conn).get_columns("class_videos")}
            self.assertEqual(set(restored), {"id", "batch_id", "title", "video_url", "description", "created_at"})
            self.assertFalse(restored["video_url"]["nullable"])
            self.assertEqual(conn.execute(text("SELECT count(*) FROM class_videos")).scalar(), 1)
