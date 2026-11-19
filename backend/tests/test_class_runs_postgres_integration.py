"""Class runs, the job queue and recordings against a marked disposable PostgreSQL.

These cover what SQLite cannot: real row locks (SELECT ... FOR UPDATE), the
SKIP LOCKED claim path, partial unique indexes, PostgreSQL enum types, and
ON DELETE SET NULL. Skipped unless the project's disposable-QA-database guard
passes (see tests/postgres_safety.py).
"""
import tempfile
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from threading import Barrier
from unittest.mock import patch

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.core import class_lifecycle as lc
from app.core import class_runs, job_queue
from app.core.class_runs import utcnow
from app.core.scheduling import business_tz
from app.crud.audit import write_audit_event
from app.database import engine
from app.models.audit_log import AuthAuditLog, AuthEventType
from app.models.batch import Batch, BatchStatusEnum
from app.models.batch_resources import ClassVideo
from app.models.class_run import BackgroundJob, ClassRecording, ClassSessionRun, RecordingEvent
from app.models.class_session import ClassSession
from app.models.user import RoleEnum, User
from tests.postgres_safety import isolated_postgres_enabled


@unittest.skipUnless(isolated_postgres_enabled(), "Requires marked disposable PostgreSQL")
class ClassRunsPostgresTests(unittest.TestCase):
    def setUp(self):
        self.tag = uuid.uuid4().hex[:10]
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        for p in (patch.object(settings, "recording_enabled", True),
                  patch.object(settings, "livekit_url", "wss://x.example.test"),
                  patch.object(settings, "livekit_api_key", "k"),
                  patch.object(settings, "livekit_api_secret", "s" * 40),
                  patch.object(settings, "recording_staging_dir", self.tmp.name),
                  patch.object(settings, "recording_min_free_gb", 0.0)):
            p.start()
            self.addCleanup(p.stop)
        with Session(bind=engine) as db:
            self.trainer = User(username=f"t-{self.tag}", full_name="T", role=RoleEnum.staff, password_hash="x",
                                is_active=True, must_change_password=False)
            db.add(self.trainer)
            db.flush()
            self.batch = Batch(name=f"B-{self.tag}", trainer_id=self.trainer.id, status=BatchStatusEnum.active)
            db.add(self.batch)
            db.commit()
            self.trainer_id, self.batch_id = self.trainer.id, self.batch.id
        self.addCleanup(self.cleanup)

    def cleanup(self):
        with Session(bind=engine) as db:
            run_ids = db.scalars(select(ClassSessionRun.id).where(ClassSessionRun.batch_id == self.batch_id)).all()
            db.execute(delete(ClassVideo).where(ClassVideo.batch_id == self.batch_id))
            db.execute(delete(RecordingEvent).where(RecordingEvent.run_id.in_(run_ids)))
            db.execute(delete(ClassRecording).where(ClassRecording.batch_id == self.batch_id))
            db.execute(delete(ClassSessionRun).where(ClassSessionRun.batch_id == self.batch_id))
            db.execute(delete(BackgroundJob).where(BackgroundJob.dedupe_key.like(f"%{self.tag}%")))
            db.execute(delete(ClassSession).where(ClassSession.batch_id == self.batch_id))
            db.execute(delete(AuthAuditLog).where(AuthAuditLog.target.like(f"%{self.tag}%")))
            db.execute(delete(Batch).where(Batch.id == self.batch_id))
            db.execute(delete(User).where(User.id == self.trainer_id))
            db.commit()

    def make_run(self, **kw):
        now = utcnow()
        defaults = dict(occurrence_key=f"pg-{self.tag}-{uuid.uuid4().hex[:6]}", kind="SCHEDULED", batch_id=self.batch_id,
                        trainer_id=self.trainer_id, state="ROOM_OPEN", scheduled_start_at=now,
                        scheduled_end_at=now + timedelta(hours=1), effective_end_at=now + timedelta(hours=1),
                        approval_status="PENDING", recording_state="AWAITING_APPROVAL")
        defaults.update(kw)
        with Session(bind=engine) as db:
            run = ClassSessionRun(**defaults)
            db.add(run)
            db.commit()
            return run.id

    # -- queue ------------------------------------------------------------------
    def test_skip_locked_claims_never_overlap_across_connections(self):
        total, workers = 60, 10
        with Session(bind=engine) as db:
            for i in range(total):
                job_queue.enqueue(db, "pg.test", f"pg-{self.tag}-{i}", {})
            db.commit()
        barrier, claimed = Barrier(workers), []

        def work(n):
            barrier.wait()
            mine = []
            with Session(bind=engine) as db:
                while True:
                    job = job_queue.claim_next(db, f"w{n}", kinds=["pg.test"])
                    if job is None:
                        return mine
                    mine.append(job.id)

        with ThreadPoolExecutor(workers) as pool:
            for result in pool.map(work, range(workers)):
                claimed += result
        self.assertEqual(len(claimed), total)
        self.assertEqual(len(set(claimed)), total, "a job was claimed by two connections")

    def test_the_same_job_enqueued_concurrently_exists_once(self):
        barrier = Barrier(8)

        def work(_):
            barrier.wait()
            with Session(bind=engine) as db:
                created = job_queue.enqueue(db, "pg.dup", f"dup-{self.tag}", {})
                db.commit()
                return created

        with ThreadPoolExecutor(8) as pool:
            results = list(pool.map(work, range(8)))
        with Session(bind=engine) as db:
            rows = db.scalar(select(func.count()).select_from(BackgroundJob).where(BackgroundJob.dedupe_key == f"dup-{self.tag}"))
        self.assertEqual(rows, 1)
        self.assertGreaterEqual(results.count(True), 1)

    # -- approval -----------------------------------------------------------------
    def test_concurrent_duplicate_approvals_produce_exactly_one_recording_job(self):
        run_id = self.make_run()
        barrier = Barrier(8)

        def work(_):
            barrier.wait()
            with Session(bind=engine) as db:
                trainer = db.get(User, self.trainer_id)
                _, changed = class_runs.decide_approval(db, trainer, run_id, decision="approve",
                                                        consent_version=settings.recording_consent_version, ip=None, ua=None)
                return changed

        with ThreadPoolExecutor(8) as pool:
            changed = list(pool.map(work, range(8)))
        self.assertEqual(changed.count(True), 1)  # the row lock let exactly one request make the change
        with Session(bind=engine) as db:
            jobs = db.scalar(select(func.count()).select_from(BackgroundJob).where(BackgroundJob.dedupe_key == f"recording.start:{run_id}"))
            granted = db.scalar(select(func.count()).select_from(AuthAuditLog).where(
                AuthAuditLog.event_type == AuthEventType.recording_approval_granted, AuthAuditLog.target == f"run:{run_id}"))
        self.assertEqual((jobs, granted), (1, 1))
        with Session(bind=engine) as db:
            db.execute(delete(BackgroundJob).where(BackgroundJob.dedupe_key == f"recording.start:{run_id}"))
            db.execute(delete(AuthAuditLog).where(AuthAuditLog.target == f"run:{run_id}"))
            db.execute(delete(RecordingEvent).where(RecordingEvent.run_id == run_id))
            db.commit()

    # -- constraints ----------------------------------------------------------------
    def test_partial_unique_indexes_hold_on_postgresql(self):
        run_id = self.make_run()
        with Session(bind=engine) as db:
            db.add(ClassRecording(run_id=run_id, batch_id=self.batch_id, capture_state="STARTING"))
            db.commit()
            db.add(ClassRecording(run_id=run_id, batch_id=self.batch_id, capture_state="RECORDING", attempt_no=2))
            with self.assertRaises(IntegrityError):
                db.commit()  # a second live recording for one class
            db.rollback()
            db.execute(ClassRecording.__table__.update().where(ClassRecording.run_id == run_id).values(capture_state="FAILED"))
            db.add(ClassRecording(run_id=run_id, batch_id=self.batch_id, capture_state="STARTING", attempt_no=2))
            db.commit()  # allowed once the earlier attempt has FAILED
        self.make_run(kind="AD_HOC", occurrence_key=f"adhoc-{self.tag}-1")
        with self.assertRaises(IntegrityError):
            self.make_run(kind="AD_HOC", occurrence_key=f"adhoc-{self.tag}-2")  # second open ad-hoc for the batch

    def test_state_check_constraints_reject_invalid_values(self):
        with self.assertRaises(IntegrityError):
            self.make_run(state="BOGUS")

    def test_every_new_audit_event_value_exists_in_the_postgres_enum(self):
        values = ["recording_approval_requested", "recording_approval_granted", "recording_approval_declined",
                  "recording_started", "recording_stopped", "recording_processed", "recording_failed",
                  "recording_published", "recording_playback", "recording_revoked", "recording_deleted", "class_run_cancelled"]
        with Session(bind=engine) as db:
            for value in values:
                write_audit_event(db, AuthEventType(value), None, None, detail="pg enum check", module="recordings",
                                  target=f"enum-{self.tag}", status="success", commit=False)
            db.commit()
            stored = db.scalar(select(func.count()).select_from(AuthAuditLog).where(AuthAuditLog.target == f"enum-{self.tag}"))
        self.assertEqual(stored, len(values))

    def test_class_videos_enforce_one_entry_per_recording_and_a_target(self):
        run_id = self.make_run()
        with Session(bind=engine) as db:
            rec = ClassRecording(run_id=run_id, batch_id=self.batch_id, capture_state="STORED", publication_state="PUBLISHED")
            db.add(rec)
            db.flush()
            db.add(ClassVideo(batch_id=self.batch_id, title="a", source="RECORDING", recording_id=rec.id))
            db.commit()
            db.add(ClassVideo(batch_id=self.batch_id, title="dup", source="RECORDING", recording_id=rec.id))
            with self.assertRaises(IntegrityError):
                db.commit()
            db.rollback()
            db.add(ClassVideo(batch_id=self.batch_id, title="neither"))
            with self.assertRaises(IntegrityError):
                db.commit()
            db.rollback()
            db.add(ClassVideo(batch_id=self.batch_id, title="posted link", video_url="https://example.com/x"))
            db.commit()  # existing behaviour: a plain link still works

    def test_deleting_a_class_session_keeps_its_runs_and_nulls_the_link(self):
        with Session(bind=engine) as db:
            session = ClassSession(batch_id=self.batch_id, title="S", session_date=utcnow().date())
            db.add(session)
            db.commit()
            session_id = session.id
        run_id = self.make_run(class_session_id=session_id)
        with Session(bind=engine) as db:
            db.delete(db.get(ClassSession, session_id))  # what the existing admin "delete session" does
            db.commit()
            run = db.get(ClassSessionRun, run_id)
            self.assertIsNotNone(run)
            self.assertIsNone(run.class_session_id)

    # -- lifecycle under real locks --------------------------------------------------------
    def test_concurrent_reconciles_create_one_occurrence_per_session(self):
        zone = business_tz()
        start = (utcnow() + timedelta(minutes=30)).astimezone(zone)
        with Session(bind=engine) as db:
            db.add(ClassSession(batch_id=self.batch_id, title="S", session_date=start.date(),
                                start_time=start.time().replace(second=0, microsecond=0), end_time=(start + timedelta(minutes=30)).time()))
            db.commit()
        barrier = Barrier(5)

        def work(_):
            barrier.wait()
            with Session(bind=engine) as db:
                try:
                    lc.reconcile_sessions(db)
                    db.commit()
                    return None
                except Exception as exc:  # noqa: BLE001
                    db.rollback()
                    return exc

        with ThreadPoolExecutor(5) as pool:
            errors = [e for e in pool.map(work, range(5)) if e is not None]
        with Session(bind=engine) as db:
            runs = db.scalar(select(func.count()).select_from(ClassSessionRun).where(ClassSessionRun.batch_id == self.batch_id))
        self.assertEqual(runs, 1)
        self.assertFalse([e for e in errors if not isinstance(e, IntegrityError)], errors)

    def test_grace_period_flow_on_real_locks(self):
        run_id = self.make_run(state="ROOM_OPEN")
        with Session(bind=engine) as db:
            self.assertTrue(lc.mark_trainer_connected(db, run_id))
            db.commit()
        t0 = utcnow()
        with Session(bind=engine) as db:
            self.assertTrue(lc.mark_trainer_disconnected(db, run_id, now=t0))
            db.commit()
        with Session(bind=engine) as db:
            self.assertTrue(lc.grace_expired(db, run_id, t0, now=t0 + timedelta(seconds=61)))
            db.commit()
        with Session(bind=engine) as db:
            run = db.get(ClassSessionRun, run_id)
            self.assertEqual((run.state, run.end_reason), ("ENDED", "TRAINER_ABSENT_TIMEOUT"))
            db.execute(delete(BackgroundJob).where(BackgroundJob.dedupe_key.in_(
                [f"room.close:{run_id}", f"run.grace:{run_id}:{int(t0.timestamp() * 1000)}"])))
            db.execute(delete(AuthAuditLog).where(AuthAuditLog.target == f"run:{run_id}"))
            db.execute(delete(RecordingEvent).where(RecordingEvent.run_id == run_id))
            db.commit()


if __name__ == "__main__":
    unittest.main()
