"""Durable job queue, class lifecycle and recording start/stop safety.

Isolated SQLite database FILE (so several threads/workers really contend),
temp staging storage, a fake media server. No production data, no network.
"""
import json
import tempfile
import threading
import unittest
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session

from app import worker
from app.config import settings
from app.core import class_lifecycle as lc
from app.core import class_runs, job_handlers, job_queue, media_server, recording_pipeline  # noqa: F401
from app.core.class_runs import as_utc, utcnow
from app.core.job_queue import PermanentFailure, RetryLater
from app.core.media_server import MediaServerNotConfigured, MediaServerUnavailable, room_name_for_run
from app.core.rate_limit import limiter
from app.core.scheduling import business_tz, local_to_utc
from app.core.security import create_access_token
from app.database import Base, get_db
from app.main import app
from app.models import (AuthAuditLog, AuthSession, Batch, BatchEnrollment, BatchStatusEnum, ClassSession,
                        Meeting, MeetingParticipant, RoleEnum, User)
from app.models.audit_log import AuthEventType
from app.models.class_run import (BackgroundJob, ClassRecording, ClassSessionRun, JOB_DEAD, JOB_PENDING, JOB_RUNNING,
                                  JOB_SUCCEEDED, RecordingEvent)
from app.models.meeting import MeetingStatus, ParticipantStatus


@compiles(JSONB, "sqlite")
def _jsonb_sqlite(type_, compiler, **kwargs):
    return "JSON"


class QaSession(Session):
    pass


@event.listens_for(QaSession, "loaded_as_persistent")
def _restore_session_timezone(session, instance):
    if isinstance(instance, AuthSession) and instance.expires_at.tzinfo is None:
        instance.expires_at = instance.expires_at.replace(tzinfo=timezone.utc)


def _user(username, role):
    return User(username=username, full_name=username.title(), role=role, password_hash="unused",
                is_active=True, must_change_password=False)


class FakeMedia:
    def __init__(self):
        self.deleted, self.started, self.stopped, self.active = [], [], [], {}
        self.rooms, self.present = [], {}
        self.fail_with = None
        self.during_start = None

    def ensure_room(self, room_name, metadata=""):
        if self.fail_with:
            raise self.fail_with
        self.rooms.append(room_name)

    def is_participant_present(self, room_name, identity):
        return self.present.get((room_name, identity), False)

    def delete_room(self, room_name):
        if self.fail_with:
            raise self.fail_with
        self.deleted.append(room_name)

    def find_active_recording(self, room_name):
        return self.active.get(room_name)

    def start_room_recording(self, room_name, staging_key):
        if self.fail_with:
            raise self.fail_with
        egress = f"EG_{len(self.started) + 1}"
        self.started.append((room_name, staging_key))
        self.active[room_name] = egress
        if self.during_start:
            self.during_start()
        return egress

    def stop_recording(self, egress_id):
        if self.fail_with:
            raise self.fail_with
        self.stopped.append(egress_id)


class Harness(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="arckenites-lifecycle-", ignore_cleanup_errors=True)
        self.storage = Path(self.temp.name) / "recordings"
        self.storage.mkdir()
        self.engine = create_engine(f"sqlite:///{Path(self.temp.name) / 'q.sqlite'}",
                                    connect_args={"check_same_thread": False, "timeout": 30})

        @event.listens_for(self.engine, "connect")
        def _fk(connection, record):
            connection.execute("PRAGMA foreign_keys=ON")

        Base.metadata.create_all(self.engine)
        with self.sf() as db:
            users = [_user("trainer", RoleEnum.staff), _user("other-trainer", RoleEnum.staff),
                     _user("student", RoleEnum.student), _user("outsider", RoleEnum.student)]
            db.add_all(users)
            db.flush()
            self.ids = {u.username: u.id for u in users}
            batch = Batch(name="Batch A", trainer_id=self.ids["trainer"], status=BatchStatusEnum.active)
            other = Batch(name="Batch B", trainer_id=self.ids["other-trainer"], status=BatchStatusEnum.active)
            db.add_all([batch, other])
            db.flush()
            self.batch_id, self.other_batch_id = batch.id, other.id
            db.add(BatchEnrollment(batch_id=batch.id, student_id=self.ids["student"]))
            db.add(BatchEnrollment(batch_id=other.id, student_id=self.ids["outsider"]))
            db.commit()

        self.media = FakeMedia()
        media_server.set_media_server_factory(lambda: self.media)
        self.patches = [
            patch.object(settings, "recording_enabled", True),
            patch.object(settings, "livekit_url", "wss://media.example.test"),
            patch.object(settings, "livekit_api_key", "APItestkey"),
            patch.object(settings, "livekit_api_secret", "test-livekit-secret-" + "x" * 32),
            patch.object(settings, "recording_staging_dir", str(self.storage)),
            patch.object(settings, "recording_min_free_gb", 0.0),
            patch.object(settings, "trainer_reconnect_grace_seconds", 60),
        ]
        for p in self.patches:
            p.start()
        self.overrides = app.dependency_overrides.copy()
        app.dependency_overrides[get_db] = self._db
        self.client = TestClient(app)
        self.limiter_enabled = limiter.enabled
        limiter.enabled = False
        self.registered = set(job_queue.HANDLERS)

    def tearDown(self):
        for kind in set(job_queue.HANDLERS) - self.registered:
            job_queue.HANDLERS.pop(kind, None)
            job_queue.DEAD_HANDLERS.pop(kind, None)
        for p in self.patches:
            p.stop()
        media_server.set_media_server_factory(None)
        self.client.close()
        app.dependency_overrides.clear()
        app.dependency_overrides.update(self.overrides)
        limiter.enabled = self.limiter_enabled
        self.engine.dispose()
        self.temp.cleanup()

    # -- helpers -------------------------------------------------------------
    def sf(self):
        return QaSession(self.engine)

    def _db(self):
        with self.sf() as db:
            yield db

    def headers(self, name):
        with self.sf() as db:
            user = db.get(User, self.ids[name])
            jti = uuid4()
            token, expires = create_access_token(user.id, user.role.value, jti)
            db.add(AuthSession(id=jti, user_id=user.id, expires_at=expires))
            db.commit()
        return {"Authorization": f"Bearer {token}"}

    def make_run(self, *, state="ROOM_OPEN", approval="PENDING", rec="AWAITING_APPROVAL", kind="SCHEDULED",
                 batch_id=None, start_min=-10, end_min=50, key=None, session_id=None):
        now = utcnow()
        with self.sf() as db:
            run = ClassSessionRun(
                occurrence_key=key or f"test-{uuid4().hex}", kind=kind, batch_id=batch_id or self.batch_id,
                class_session_id=session_id, trainer_id=self.ids["trainer"], state=state,
                scheduled_start_at=now + timedelta(minutes=start_min), scheduled_end_at=now + timedelta(minutes=end_min),
                effective_end_at=now + timedelta(minutes=end_min), approval_status=approval, recording_state=rec,
                approval_source="TRAINER" if approval == "APPROVED" else None)
            db.add(run)
            db.commit()
            return run.id

    def run_row(self, run_id):
        with self.sf() as db:
            row = db.get(ClassSessionRun, run_id)
            db.expunge(row)
            return row

    def jobs(self, kind=None):
        with self.sf() as db:
            query = select(BackgroundJob).order_by(BackgroundJob.id)
            if kind:
                query = query.where(BackgroundJob.kind == kind)
            rows = db.scalars(query).all()
            db.expunge_all()
            return rows

    def count(self, model, *where):
        with self.sf() as db:
            return db.scalar(select(func.count()).select_from(model).where(*where))

    def act(self, fn, *args, **kwargs):
        with self.sf() as db:
            result = fn(db, *args, **kwargs)
            db.commit()
            return result

    def recordings(self):
        with self.sf() as db:
            rows = db.scalars(select(ClassRecording)).all()
            db.expunge_all()
            return rows

    def add_session(self, minutes_from_now=30, length=60, batch_id=None):
        zone = business_tz()
        start_local = (utcnow() + timedelta(minutes=minutes_from_now)).astimezone(zone)
        end_local = start_local + timedelta(minutes=length)
        with self.sf() as db:
            session = ClassSession(batch_id=batch_id or self.batch_id, title="Class", session_date=start_local.date(),
                                   start_time=start_local.time().replace(second=0, microsecond=0),
                                   end_time=end_local.time().replace(second=0, microsecond=0))
            if end_local.date() != start_local.date():
                session.end_time = time(23, 59)
            db.add(session)
            db.commit()
            return session.id

    def drain(self, worker_id="w1", limit=50):
        return worker.drain(self.sf, worker_id, limit)


# ===========================================================================
# Queue
# ===========================================================================

class JobQueueTests(Harness):
    def enqueue(self, kind="test.job", key=None, **kw):
        with self.sf() as db:
            created = job_queue.enqueue(db, kind, key or f"k-{uuid4().hex}", {"x": 1}, **kw)
            db.commit()
            return created

    def test_duplicate_jobs_are_deduplicated(self):
        self.assertTrue(self.enqueue(key="same"))
        self.assertFalse(self.enqueue(key="same"))
        self.assertEqual(self.count(BackgroundJob), 1)

    def test_a_future_job_is_not_claimed_until_due(self):
        self.enqueue(run_at=utcnow() + timedelta(minutes=5))
        with self.sf() as db:
            self.assertIsNone(job_queue.claim_next(db, "w1"))
            self.assertIsNotNone(job_queue.claim_next(db, "w1", now=utcnow() + timedelta(minutes=6)))

    def test_claim_takes_a_lease_and_a_second_worker_cannot_take_the_job(self):
        self.enqueue()
        with self.sf() as a, self.sf() as b:
            job = job_queue.claim_next(a, "w1")
            self.assertEqual((job.state, job.locked_by, job.attempts), (JOB_RUNNING, "w1", 1))
            self.assertIsNone(job_queue.claim_next(b, "w2"))

    def test_concurrent_workers_never_claim_the_same_job(self):
        total, workers = 40, 8
        for i in range(total):
            self.enqueue(key=f"job-{i}")
        claimed, errors, barrier = [], [], threading.Barrier(workers)

        def work(name):
            barrier.wait()
            try:
                with self.sf() as db:
                    while True:
                        job = job_queue.claim_next(db, name)
                        if job is None:
                            return
                        claimed.append(job.id)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=work, args=(f"w{i}",)) for i in range(workers)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(60)
        self.assertEqual(errors, [])
        self.assertEqual(len(claimed), total)
        self.assertEqual(len(set(claimed)), total, "a job was claimed by more than one worker")

    def test_concurrent_workers_execute_a_job_exactly_once(self):
        ran = []
        job_queue.HANDLERS["test.once"] = lambda db, job: ran.append(job.id)
        self.enqueue(kind="test.once", key="only-one")
        barrier = threading.Barrier(6)

        def work(name):
            barrier.wait()
            job_queue.run_one(self.sf, name)

        threads = [threading.Thread(target=work, args=(f"w{i}",)) for i in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(60)
        self.assertEqual(len(ran), 1)
        self.assertEqual(self.jobs("test.once")[0].state, JOB_SUCCEEDED)

    def test_expired_lease_is_recovered_and_the_stale_worker_is_fenced_out(self):
        self.enqueue()
        t0 = utcnow()
        with self.sf() as a:
            first = job_queue.claim_next(a, "w1", now=t0, lease_seconds=120)
            self.assertIsNone(job_queue.claim_next(a, "w2", now=t0 + timedelta(seconds=60)))  # still leased
            second = job_queue.claim_next(a, "w2", now=t0 + timedelta(seconds=121))
            self.assertEqual((second.id, second.locked_by, second.attempts), (first.id, "w2", 2))
            # the crashed/slow first worker wakes up and tries to finish: refused
            self.assertFalse(job_queue.complete(a, first, "w1"))
            self.assertIsNone(job_queue.fail(a, first, "w1", "late"))
            a.commit()
            self.assertTrue(job_queue.complete(a, second, "w2"))

    def test_a_job_that_keeps_losing_its_lease_goes_dead_instead_of_looping_forever(self):
        dead = []
        job_queue.DEAD_HANDLERS["test.expire"] = lambda db, job: dead.append(job.id)
        self.enqueue(kind="test.expire", max_attempts=2)
        t = utcnow()
        with self.sf() as db:
            self.assertIsNotNone(job_queue.claim_next(db, "w1", now=t, lease_seconds=10))
            self.assertIsNotNone(job_queue.claim_next(db, "w2", now=t + timedelta(seconds=11), lease_seconds=10))
            self.assertIsNone(job_queue.claim_next(db, "w3", now=t + timedelta(seconds=22), lease_seconds=10))
        job = self.jobs("test.expire")[0]
        self.assertEqual(job.state, JOB_DEAD)
        self.assertEqual(dead, [job.id])

    def test_failures_back_off_exponentially_then_go_dead(self):
        dead = []
        job_queue.HANDLERS["test.fail"] = lambda db, job: (_ for _ in ()).throw(ValueError("boom"))
        job_queue.DEAD_HANDLERS["test.fail"] = lambda db, job: dead.append(job.id)
        self.enqueue(kind="test.fail", max_attempts=3)
        delays = []
        for attempt in (1, 2):
            self.assertTrue(job_queue.run_one(self.sf, "w1"))
            job = self.jobs("test.fail")[0]
            self.assertEqual((job.state, job.attempts), (JOB_PENDING, attempt))
            self.assertIn("ValueError", job.last_error)
            delays.append((as_utc(job.run_at) - utcnow()).total_seconds())
            self.assertFalse(job_queue.run_one(self.sf, "w1"))  # not due yet, so not claimable
            with self.sf() as db:
                db.get(BackgroundJob, job.id).run_at = utcnow() - timedelta(seconds=1)
                db.commit()
        self.assertGreater(delays[1], delays[0] * 1.5)  # exponential, not constant
        self.assertTrue(job_queue.run_one(self.sf, "w1"))  # third and last attempt
        job = self.jobs("test.fail")[0]
        self.assertEqual((job.state, job.attempts), (JOB_DEAD, 3))
        self.assertEqual(dead, [job.id])

    def test_permanent_failure_goes_dead_immediately_and_retry_later_honours_its_delay(self):
        def permanent(db, job):
            raise PermanentFailure("no point retrying")

        def later(db, job):
            raise RetryLater("not yet", delay_seconds=500)

        job_queue.HANDLERS["test.perm"], job_queue.HANDLERS["test.later"] = permanent, later
        self.enqueue(kind="test.perm")
        self.enqueue(kind="test.later")
        self.drain()
        perm, soon = self.jobs("test.perm")[0], self.jobs("test.later")[0]
        self.assertEqual((perm.state, perm.attempts), (JOB_DEAD, 1))
        self.assertEqual(soon.state, JOB_PENDING)
        self.assertAlmostEqual((as_utc(soon.run_at) - utcnow()).total_seconds(), 500, delta=5)

    def test_a_job_with_no_handler_is_dead_not_retried(self):
        self.enqueue(kind="test.unknown")
        self.drain()
        self.assertEqual(self.jobs("test.unknown")[0].state, JOB_DEAD)

    def test_worker_restart_recovers_a_job_whose_worker_crashed(self):
        ran = []
        job_queue.HANDLERS["test.crash"] = lambda db, job: ran.append(job.attempts)
        self.enqueue(kind="test.crash")
        with self.sf() as db:  # worker A claims it, then the process is killed
            job_queue.claim_next(db, "worker-A")
        self.assertFalse(job_queue.run_one(self.sf, "worker-B"))  # lease still valid: B must not steal it
        with self.sf() as db:  # time passes; the lease runs out
            db.get(BackgroundJob, self.jobs("test.crash")[0].id).lease_until = utcnow() - timedelta(seconds=1)
            db.commit()
        self.assertTrue(job_queue.run_one(self.sf, "worker-B"))  # a freshly restarted worker picks it up
        self.assertEqual(ran, [2])
        self.assertEqual(self.jobs("test.crash")[0].state, JOB_SUCCEEDED)

    def test_a_handler_that_lost_its_lease_cannot_complete_the_job(self):
        def slow(db, job):
            with self.sf() as other:  # the lease expires and worker B takes over mid-run
                other.get(BackgroundJob, job.id).lease_until = utcnow() - timedelta(seconds=1)
                other.commit()
                self.assertIsNotNone(job_queue.claim_next(other, "worker-B"))

        job_queue.HANDLERS["test.slow"] = slow
        self.enqueue(kind="test.slow")
        job_queue.run_one(self.sf, "worker-A")
        job = self.jobs("test.slow")[0]
        self.assertEqual((job.state, job.locked_by), (JOB_RUNNING, "worker-B"))

    def test_only_old_successful_reconcile_jobs_are_pruned(self):
        old = utcnow() - timedelta(hours=2)
        with self.sf() as db:
            for kind, key, state in (("sessions.reconcile", "r-old", JOB_SUCCEEDED), ("sessions.reconcile", "r-dead", JOB_DEAD),
                                     ("recording.start", "s-old", JOB_SUCCEEDED)):
                db.add(BackgroundJob(kind=kind, dedupe_key=key, payload="{}", state=state, run_at=old,
                                     completed_at=old, attempts=1, max_attempts=3))
            db.commit()
        worker.enqueue_periodic(self.sf)
        keys = {j.dedupe_key for j in self.jobs()}
        self.assertNotIn("r-old", keys)          # pruned
        self.assertIn("r-dead", keys)            # failures are kept for diagnosis
        self.assertIn("s-old", keys)             # other job kinds are never pruned

    def test_periodic_reconcile_is_enqueued_once_per_bucket(self):
        worker.enqueue_periodic(self.sf)
        worker.enqueue_periodic(self.sf)
        self.assertEqual(len(self.jobs("sessions.reconcile")), 1)


# ===========================================================================
# Lifecycle
# ===========================================================================

class LifecycleTests(Harness):
    def test_activation_opens_the_room_once_and_creates_no_attendance_or_recording(self):
        self.add_session(minutes_from_now=2)
        self.act(lc.reconcile_sessions)
        with self.sf() as db:
            run_id = db.scalars(select(ClassSessionRun.id)).one()
        self.assertTrue(self.act(lc.activate_run, run_id))
        self.assertFalse(self.act(lc.activate_run, run_id))  # duplicate delivery
        row = self.run_row(run_id)
        self.assertEqual((row.state, row.recording_state, row.approval_status), ("ROOM_OPEN", "AWAITING_APPROVAL", "PENDING"))
        self.assertIsNone(row.actual_start_at)  # nobody has actually started the lesson
        self.assertEqual(len(self.jobs("run.notify_open")), 1)
        self.assertEqual(self.count(MeetingParticipant), 0)
        self.assertEqual(self.count(ClassRecording), 0)

    def test_activation_cancels_an_occurrence_whose_session_no_longer_exists(self):
        run_id = self.make_run(state="SCHEDULED", start_min=2)  # a scheduled occurrence with no session behind it
        self.assertFalse(self.act(lc.activate_run, run_id))
        row = self.run_row(run_id)
        self.assertEqual((row.state, row.end_reason), ("CANCELLED", "SESSION_DELETED"))
        self.assertEqual(self.jobs("run.notify_open"), [])

    def test_activation_never_resurrects_a_cancelled_run(self):
        run_id = self.make_run(state="CANCELLED")
        self.assertFalse(self.act(lc.activate_run, run_id))
        self.assertEqual(self.run_row(run_id).state, "CANCELLED")

    def test_time_driven_jobs_are_created_once_and_at_the_right_time(self):
        run_id = self.make_run(state="SCHEDULED", start_min=30, end_min=90)
        for _ in range(3):
            with self.sf() as db:
                lc.schedule_run_jobs(db, db.get(ClassSessionRun, run_id))
                db.commit()
        self.assertEqual(len(self.jobs()), 2)
        activate = self.jobs("session.activate")[0]
        row = self.run_row(run_id)
        self.assertEqual(as_utc(activate.run_at), as_utc(row.scheduled_start_at) - timedelta(minutes=settings.class_room_open_lead_minutes))
        self.assertEqual(as_utc(self.jobs("run.scheduled_end")[0].run_at), as_utc(row.effective_end_at))

    def test_trainer_connecting_makes_the_class_live_but_never_starts_recording(self):
        run_id = self.make_run()
        self.assertTrue(self.act(lc.mark_trainer_connected, run_id))
        row = self.run_row(run_id)
        self.assertEqual(row.state, "LIVE")
        self.assertIsNotNone(row.actual_start_at)
        self.assertEqual((row.approval_status, row.recording_state), ("PENDING", "AWAITING_APPROVAL"))
        self.assertEqual(self.count(ClassRecording), 0)
        self.assertEqual(self.jobs("recording.start"), [])

    def test_disconnect_starts_the_grace_period_and_does_not_end_the_class(self):
        run_id = self.make_run(state="LIVE")
        t0 = utcnow()
        self.assertTrue(self.act(lc.mark_trainer_disconnected, run_id, now=t0))
        row = self.run_row(run_id)
        self.assertEqual(row.state, "LIVE")
        self.assertEqual(as_utc(row.trainer_left_at), t0)
        grace = self.jobs("run.grace_expiry")[0]
        self.assertEqual(as_utc(grace.run_at), t0 + timedelta(seconds=60))

    def test_rejoining_inside_the_grace_period_cancels_the_termination(self):
        run_id = self.make_run(state="LIVE")
        t0 = utcnow()
        self.act(lc.mark_trainer_disconnected, run_id, now=t0)
        self.act(lc.mark_trainer_connected, run_id, now=t0 + timedelta(seconds=30))
        self.assertIsNone(self.run_row(run_id).trainer_left_at)
        # the queued grace job fires after its deadline and finds nothing to do
        self.assertFalse(self.act(lc.grace_expired, run_id, t0, now=t0 + timedelta(seconds=120)))
        self.assertEqual(self.run_row(run_id).state, "LIVE")

    def test_grace_period_expiry_ends_the_class_for_everyone(self):
        run_id = self.make_run(state="LIVE")
        with self.sf() as db:
            meeting = Meeting(title="m", host_id=self.ids["trainer"], scheduled_at=utcnow(), status=MeetingStatus.LIVE)
            db.add(meeting)
            db.flush()
            db.add(MeetingParticipant(meeting_id=meeting.id, user_id=self.ids["student"], status=ParticipantStatus.JOINED))
            db.get(ClassSessionRun, run_id).meeting_id = meeting.id
            db.commit()
        t0 = utcnow()
        self.act(lc.mark_trainer_disconnected, run_id, now=t0)
        with self.assertRaises(RetryLater):  # one second before the deadline
            self.act(lc.grace_expired, run_id, t0, now=t0 + timedelta(seconds=59))
        self.assertTrue(self.act(lc.grace_expired, run_id, t0, now=t0 + timedelta(seconds=61)))
        row = self.run_row(run_id)
        self.assertEqual((row.state, row.end_reason), ("ENDED", "TRAINER_ABSENT_TIMEOUT"))
        self.assertEqual(len(self.jobs("room.close")), 1)
        with self.sf() as db:
            self.assertEqual(db.scalars(select(MeetingParticipant.status)).one(), ParticipantStatus.LEFT)
            self.assertEqual(db.scalars(select(Meeting.status)).one(), MeetingStatus.COMPLETED)

    def test_a_stale_grace_job_from_an_earlier_drop_cannot_end_a_later_session(self):
        run_id = self.make_run(state="LIVE")
        t1 = utcnow() - timedelta(minutes=10)
        self.act(lc.mark_trainer_disconnected, run_id, now=t1)
        self.act(lc.mark_trainer_connected, run_id, now=t1 + timedelta(seconds=20))
        t2 = utcnow()
        self.act(lc.mark_trainer_disconnected, run_id, now=t2)
        self.assertFalse(self.act(lc.grace_expired, run_id, t1, now=t2 + timedelta(seconds=5)))  # old job: no-op
        self.assertEqual(self.run_row(run_id).state, "LIVE")
        self.assertTrue(self.act(lc.grace_expired, run_id, t2, now=t2 + timedelta(seconds=61)))

    def test_out_of_order_presence_events_are_ignored(self):
        run_id = self.make_run(state="ROOM_OPEN")
        base = utcnow()
        self.act(lc.mark_trainer_connected, run_id, event_at=base + timedelta(seconds=10))
        # a "disconnected" event stamped BEFORE the connect arrives late
        self.assertFalse(self.act(lc.mark_trainer_disconnected, run_id, event_at=base + timedelta(seconds=5)))
        self.assertFalse(self.act(lc.mark_trainer_connected, run_id, event_at=base + timedelta(seconds=10)))  # duplicate
        row = self.run_row(run_id)
        self.assertEqual((row.state, row.trainer_left_at), ("LIVE", None))
        self.assertEqual(self.jobs("run.grace_expiry"), [])

    def test_late_presence_events_for_a_finished_class_change_nothing(self):
        run_id = self.make_run(state="ENDED")
        self.assertFalse(self.act(lc.mark_trainer_connected, run_id))
        self.assertFalse(self.act(lc.mark_trainer_disconnected, run_id))
        self.assertEqual(self.run_row(run_id).state, "ENDED")

    # -- explicit end / leave / extend (API) ---------------------------------
    def test_trainer_ending_the_class_terminates_it_and_starts_stopping_the_recording(self):
        run_id = self.make_run(state="LIVE", approval="APPROVED", rec="RECORDING")
        with self.sf() as db:
            db.add(ClassRecording(run_id=run_id, batch_id=self.batch_id, capture_state="RECORDING", egress_id="EG_X",
                                  staging_key="incoming/x.mp4"))
            db.commit()
        r = self.client.post(f"/api/class-runs/{run_id}/end", headers=self.headers("trainer"))
        self.assertEqual(r.status_code, 200, r.text)
        row = self.run_row(run_id)
        self.assertEqual((row.state, row.end_reason, row.recording_state), ("ENDED", "TRAINER_ENDED", "STOPPING"))
        stop = self.jobs("recording.stop")
        self.assertEqual(len(stop), 1)
        self.assertLessEqual(as_utc(stop[0].run_at), utcnow())  # immediately, not at the scheduled end
        self.assertEqual(len(self.jobs("room.close")), 1)
        self.assertEqual(self.client.post(f"/api/class-runs/{run_id}/end", headers=self.headers("trainer")).status_code, 200)
        self.assertEqual(len(self.jobs("recording.stop")), 1)  # idempotent

    def test_trainer_leaving_for_good_ends_it_for_everyone(self):
        run_id = self.make_run(state="LIVE")
        self.assertEqual(self.client.post(f"/api/class-runs/{run_id}/trainer-leave", headers=self.headers("trainer")).status_code, 200)
        row = self.run_row(run_id)
        self.assertEqual((row.state, row.end_reason), ("ENDED", "TRAINER_LEFT"))

    def test_only_the_class_trainer_can_end_leave_or_extend(self):
        run_id = self.make_run(state="LIVE")
        for name, expected in (("student", 403), ("outsider", 404), ("other-trainer", 404)):
            with self.subTest(name):
                for path, body in (("end", None), ("trainer-leave", None), ("extend", {"minutes": 10})):
                    r = self.client.post(f"/api/class-runs/{run_id}/{path}", headers=self.headers(name), json=body)
                    self.assertEqual(r.status_code, expected, f"{path}: {r.text}")
        self.assertEqual(self.run_row(run_id).state, "LIVE")

    def test_ending_before_a_decision_or_after_a_decline_never_records(self):
        pending = self.make_run(state="LIVE")
        declined = self.make_run(state="LIVE", approval="DECLINED", rec="DECLINED")
        for run_id in (pending, declined):
            self.client.post(f"/api/class-runs/{run_id}/end", headers=self.headers("trainer"))
        self.assertEqual(self.run_row(pending).recording_state, "NOT_RECORDED")
        self.assertEqual(self.run_row(declined).recording_state, "DECLINED")
        self.assertEqual(self.jobs("recording.stop"), [])

    def test_scheduled_end_closes_a_live_class_and_misses_one_nobody_led(self):
        live, empty = self.make_run(state="LIVE", end_min=-1), self.make_run(state="ROOM_OPEN", end_min=-1)
        for run_id in (live, empty):
            end_at = self.run_row(run_id).effective_end_at
            self.act(lc.scheduled_end, run_id, end_at)
        self.assertEqual((self.run_row(live).state, self.run_row(live).end_reason), ("ENDED", "SCHEDULED_END"))
        missed = self.run_row(empty)
        self.assertEqual((missed.state, missed.end_reason, missed.recording_state), ("MISSED", "NO_SHOW", "NOT_RECORDED"))
        self.assertEqual(self.count(MeetingParticipant), 0)  # a class nobody led has no attendance

    def test_scheduled_end_waits_until_the_end_time(self):
        run_id = self.make_run(state="LIVE", end_min=30)
        with self.assertRaises(RetryLater):
            self.act(lc.scheduled_end, run_id, self.run_row(run_id).effective_end_at)

    def test_extension_moves_the_end_and_the_old_end_job_becomes_a_no_op(self):
        run_id = self.make_run(state="LIVE", end_min=-1)
        old_end = self.run_row(run_id).effective_end_at
        with self.sf() as db:
            lc.schedule_run_jobs(db, db.get(ClassSessionRun, run_id))
            db.commit()
        r = self.client.post(f"/api/class-runs/{run_id}/extend", headers=self.headers("trainer"), json={"minutes": 30})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(as_utc(self.run_row(run_id).effective_end_at), as_utc(old_end) + timedelta(minutes=30))
        self.assertEqual(len(self.jobs("run.scheduled_end")), 2)
        self.assertFalse(self.act(lc.scheduled_end, run_id, old_end))  # the original deadline no longer applies
        self.assertEqual(self.run_row(run_id).state, "LIVE")

    def test_extension_is_capped(self):
        run_id = self.make_run(state="LIVE")
        ok = self.client.post(f"/api/class-runs/{run_id}/extend", headers=self.headers("trainer"), json={"minutes": 40})
        over = self.client.post(f"/api/class-runs/{run_id}/extend", headers=self.headers("trainer"), json={"minutes": 21})
        self.assertEqual((ok.status_code, over.status_code), (200, 409))

    def test_an_ended_class_cannot_be_extended(self):
        run_id = self.make_run(state="ENDED")
        r = self.client.post(f"/api/class-runs/{run_id}/extend", headers=self.headers("trainer"), json={"minutes": 5})
        self.assertEqual(r.status_code, 409)

    def test_a_class_that_has_not_opened_cannot_be_ended_early(self):
        run_id = self.make_run(state="SCHEDULED", start_min=60, end_min=120)
        self.assertEqual(self.client.post(f"/api/class-runs/{run_id}/end", headers=self.headers("trainer")).status_code, 409)

    # -- ad hoc ---------------------------------------------------------------
    def test_ad_hoc_classes_follow_the_same_lifecycle_and_approval_path(self):
        with self.sf() as db:
            run = lc.open_adhoc_run(db, db.get(Batch, self.batch_id))
            db.commit()
            run_id = run.id
        self.assertEqual(len(self.jobs("run.scheduled_end")), 1)
        row = self.run_row(run_id)
        self.assertEqual((row.kind, row.state, row.approval_status, row.recording_state, row.actual_start_at),
                         ("AD_HOC", "ROOM_OPEN", "PENDING", "AWAITING_APPROVAL", None))
        self.act(lc.mark_trainer_connected, run_id)
        self.assertEqual(self.run_row(run_id).state, "LIVE")
        self.client.post(f"/api/class-runs/{run_id}/end", headers=self.headers("trainer"))
        with self.sf() as db:  # the next ad-hoc class is a different occurrence needing its own approval
            nxt = lc.open_adhoc_run(db, db.get(Batch, self.batch_id))
            db.commit()
            self.assertNotEqual(nxt.id, run_id)
            self.assertEqual(nxt.approval_status, "PENDING")

    def test_an_unattended_ad_hoc_class_is_missed_not_recorded(self):
        run_id = self.make_run(kind="AD_HOC", state="ROOM_OPEN", end_min=-1)
        self.act(lc.scheduled_end, run_id, self.run_row(run_id).effective_end_at)
        self.assertEqual(self.run_row(run_id).state, "MISSED")


# ===========================================================================
# Recording start/stop safety
# ===========================================================================

class RecordingControlTests(Harness):
    def start(self, run_id):
        with self.sf() as db:
            return recording_pipeline.start_recording(db, run_id)

    def stop(self, run_id):
        with self.sf() as db:
            return recording_pipeline.stop_recording(db, run_id)

    def test_recording_never_starts_without_persisted_trainer_approval(self):
        for approval, rec in (("PENDING", "AWAITING_APPROVAL"), ("DECLINED", "DECLINED")):
            with self.subTest(approval):
                run_id = self.make_run(state="LIVE", approval=approval, rec=rec)
                self.assertEqual(self.start(run_id), "not_approved")
        self.assertEqual(self.media.started, [])
        self.assertEqual(self.recordings(), [])

    def test_a_forged_start_job_for_an_unapproved_class_starts_nothing(self):
        run_id = self.make_run(state="LIVE")
        with self.sf() as db:
            job_queue.enqueue(db, "recording.start", f"forged:{run_id}", {"run_id": run_id})
            db.commit()
        self.drain()
        self.assertEqual(self.media.started, [])
        self.assertEqual(self.recordings(), [])
        self.assertEqual(self.jobs("recording.start")[0].state, JOB_SUCCEEDED)  # processed, harmlessly

    def test_a_class_that_ended_before_the_job_ran_is_not_recorded(self):
        run_id = self.make_run(state="ENDED", approval="APPROVED", rec="APPROVED")
        self.assertEqual(self.start(run_id), "class_not_open")
        self.assertEqual(self.run_row(run_id).recording_state, "NOT_RECORDED")
        self.assertEqual(self.media.started, [])

    def test_approved_class_starts_exactly_one_recording_and_duplicates_are_harmless(self):
        run_id = self.make_run(state="LIVE", approval="APPROVED", rec="APPROVED")
        self.assertEqual(self.start(run_id), "started")
        self.assertEqual(self.start(run_id), "already_started")
        self.assertEqual(self.start(run_id), "already_started")
        rows = self.recordings()
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0].capture_state, rows[0].egress_id, rows[0].run_id), ("STARTING", "EG_1", run_id))
        self.assertTrue(rows[0].staging_key.startswith("incoming/"))
        self.assertEqual(len(self.media.started), 1)
        # STARTING, not RECORDING: only the media server's own event may say it is recording
        self.assertEqual(self.run_row(run_id).recording_state, "STARTING")

    def test_a_crash_between_starting_and_saving_adopts_the_running_recording(self):
        run_id = self.make_run(state="LIVE", approval="APPROVED", rec="STARTING")
        with self.sf() as db:  # phase 1 was saved, then the process died before saving the egress id
            db.add(ClassRecording(run_id=run_id, batch_id=self.batch_id, capture_state="STARTING", staging_key="incoming/a.mp4"))
            db.commit()
        self.media.active[room_name_for_run(run_id)] = "EG_RUNNING"
        self.assertEqual(self.start(run_id), "started")
        rows = self.recordings()
        self.assertEqual((len(rows), rows[0].egress_id), (1, "EG_RUNNING"))
        self.assertEqual(self.media.started, [])  # adopted; no second recording

    def test_a_retry_after_a_crash_with_nothing_running_starts_one_recording(self):
        run_id = self.make_run(state="LIVE", approval="APPROVED", rec="STARTING")
        with self.sf() as db:
            db.add(ClassRecording(run_id=run_id, batch_id=self.batch_id, capture_state="STARTING", staging_key="incoming/a.mp4"))
            db.commit()
        self.assertEqual(self.start(run_id), "started")
        self.assertEqual((len(self.recordings()), len(self.media.started)), (1, 1))

    def test_media_server_trouble_retries_and_is_reported_truthfully_when_it_gives_up(self):
        run_id = self.make_run(state="LIVE", approval="APPROVED", rec="APPROVED")
        with self.sf() as db:
            job_queue.enqueue(db, "recording.start", f"recording.start:{run_id}", {"run_id": run_id}, max_attempts=2)
            db.commit()
        self.media.fail_with = MediaServerUnavailable("connection refused")
        for _ in range(2):
            self.drain()
            with self.sf() as db:
                for job in db.scalars(select(BackgroundJob)):
                    job.run_at = utcnow() - timedelta(seconds=1)
                db.commit()
        job = self.jobs("recording.start")[0]
        self.assertEqual(job.state, JOB_DEAD)
        row = self.run_row(run_id)
        self.assertEqual(row.recording_state, "FAILED")  # never shows a success that did not happen
        self.assertEqual([r.capture_state for r in self.recordings()], ["FAILED"])
        self.assertEqual(self.count(AuthAuditLog, AuthAuditLog.event_type == AuthEventType.recording_failed), 1)
        self.assertEqual(self.media.started, [])

    def test_no_media_server_configured_is_never_reported_as_a_recording(self):
        media_server.set_media_server_factory(None)
        run_id = self.make_run(state="LIVE", approval="APPROVED", rec="APPROVED")
        with patch.object(settings, "livekit_api_secret", ""):  # LiveKit not configured: nothing to talk to
            # start_recording refuses at the infrastructure gate and asks to be retried
            with self.assertRaises(RetryLater):
                self.start(run_id)
            with self.assertRaises(MediaServerNotConfigured):
                media_server.get_media_server()
        self.assertEqual(self.recordings(), [])
        self.assertEqual(self.media.started, [])

    def test_unavailable_storage_blocks_the_start_with_a_retry(self):
        run_id = self.make_run(state="LIVE", approval="APPROVED", rec="APPROVED")
        with patch.object(settings, "recording_min_free_gb", 10 ** 9):
            with self.assertRaises(RetryLater):
                self.start(run_id)
        self.assertEqual(self.media.started, [])

    def test_a_class_ending_while_the_recording_is_starting_stops_it(self):
        run_id = self.make_run(state="LIVE", approval="APPROVED", rec="APPROVED")

        def end_class_midway():
            with self.sf() as other:
                lc.trainer_end(other, run_id, other.get(User, self.ids["trainer"]))
                other.commit()

        self.media.during_start = end_class_midway
        self.assertEqual(self.start(run_id), "started_then_stopped")
        self.assertEqual(self.media.stopped, ["EG_1"])
        self.assertEqual(self.recordings()[0].capture_state, "FINALIZING")

    def test_stop_finalizes_once_and_is_idempotent(self):
        run_id = self.make_run(state="ENDED", approval="APPROVED", rec="STOPPING")
        with self.sf() as db:
            db.add(ClassRecording(run_id=run_id, batch_id=self.batch_id, capture_state="RECORDING", egress_id="EG_9",
                                  staging_key="incoming/z.mp4"))
            db.commit()
        self.assertEqual(self.stop(run_id), "stopped")
        self.assertEqual(self.stop(run_id), "already_stopping")
        self.assertEqual(self.media.stopped, ["EG_9"])
        rec = self.recordings()[0]
        self.assertEqual(rec.capture_state, "FINALIZING")
        self.assertIsNotNone(rec.ended_at)

    def test_stop_retries_while_the_media_server_is_down_and_never_marks_success(self):
        run_id = self.make_run(state="ENDED", approval="APPROVED", rec="STOPPING")
        with self.sf() as db:
            db.add(ClassRecording(run_id=run_id, batch_id=self.batch_id, capture_state="RECORDING", egress_id="EG_9",
                                  staging_key="incoming/z.mp4"))
            db.commit()
        self.media.fail_with = MediaServerUnavailable("down")
        with self.assertRaises(RetryLater):
            self.stop(run_id)
        self.assertEqual(self.recordings()[0].capture_state, "RECORDING")  # unchanged: not claimed as stopped

    def test_stopping_a_recording_that_never_reached_the_media_server(self):
        run_id = self.make_run(state="ENDED", approval="APPROVED", rec="STOPPING")
        with self.sf() as db:
            db.add(ClassRecording(run_id=run_id, batch_id=self.batch_id, capture_state="STARTING", staging_key="incoming/n.mp4"))
            db.commit()
        self.assertEqual(self.stop(run_id), "never_started")
        self.assertEqual(self.recordings()[0].capture_state, "FAILED")
        self.assertEqual(self.run_row(run_id).recording_state, "NOT_RECORDED")
        self.assertEqual(self.media.stopped, [])

    def test_stop_with_no_recording_marks_the_class_not_recorded(self):
        run_id = self.make_run(state="ENDED", approval="APPROVED", rec="APPROVED")
        self.assertEqual(self.stop(run_id), "nothing_to_stop")
        self.assertEqual(self.run_row(run_id).recording_state, "NOT_RECORDED")

    def test_room_close_deletes_the_room_or_retries(self):
        run_id = self.make_run(state="ENDED")
        with self.sf() as db:
            job_queue.enqueue(db, "room.close", f"room.close:{run_id}", {"run_id": run_id})
            db.commit()
        self.drain()
        self.assertEqual(self.media.deleted, [room_name_for_run(run_id)])

    def test_room_close_with_no_media_server_is_a_harmless_noop(self):
        media_server.set_media_server_factory(None)
        run_id = self.make_run(state="ENDED")
        with self.sf() as db:
            job_queue.enqueue(db, "room.close", f"room.close:{run_id}", {"run_id": run_id})
            db.commit()
        with patch.object(settings, "livekit_api_secret", ""):  # genuinely unconfigured, so there is no room to close
            self.drain()
        self.assertEqual(self.jobs("room.close")[0].state, JOB_SUCCEEDED)


# ===========================================================================
# Reconciliation: new, rescheduled, cancelled, paused sessions; lost jobs
# ===========================================================================

class ReconcileTests(Harness):
    def reconcile(self):
        return self.act(lc.reconcile_sessions)

    def runs(self):
        with self.sf() as db:
            rows = db.scalars(select(ClassSessionRun).order_by(ClassSessionRun.id)).all()
            db.expunge_all()
            return rows

    def test_an_upcoming_session_gets_one_occurrence_and_its_jobs_without_a_browser(self):
        self.add_session()
        self.reconcile()
        self.reconcile()
        runs = self.runs()
        self.assertEqual([(r.state, r.approval_status) for r in runs], [("SCHEDULED", "PENDING")])
        self.assertEqual((len(self.jobs("session.activate")), len(self.jobs("run.scheduled_end"))), (1, 1))

    def test_sessions_that_already_ended_or_are_far_in_the_future_get_no_occurrence(self):
        self.add_session(minutes_from_now=-180, length=30)
        self.add_session(minutes_from_now=60 * 24 * 10)
        self.reconcile()
        self.assertEqual(self.runs(), [])

    def test_a_job_driven_activation_end_to_end(self):
        self.add_session(minutes_from_now=2)
        self.reconcile()
        self.drain()  # the activate job is due (room opens 5 minutes early)
        self.assertEqual(self.runs()[0].state, "ROOM_OPEN")
        self.assertEqual(len(self.jobs("run.notify_open")), 1)

    def test_rescheduling_replaces_the_occurrence_and_requires_a_new_approval(self):
        session_id = self.add_session(minutes_from_now=30)
        self.reconcile()
        old = self.runs()[0]
        with self.sf() as db:
            db.get(ClassSessionRun, old.id).approval_status = "APPROVED"  # approved for the OLD time
            session = db.get(ClassSession, session_id)
            session.start_time = (datetime.combine(session.session_date, session.start_time) + timedelta(minutes=45)).time()
            session.end_time = time(23, 59)
            db.commit()
        self.reconcile()
        old_row, new_row = self.runs()
        self.assertEqual((old_row.state, old_row.end_reason), ("CANCELLED", "RESCHEDULED"))
        self.assertEqual((new_row.state, new_row.approval_status), ("SCHEDULED", "PENDING"))
        self.assertNotEqual(old_row.occurrence_key, new_row.occurrence_key)

    def test_a_deleted_session_cancels_its_unstarted_occurrence(self):
        session_id = self.add_session()
        self.reconcile()
        with self.sf() as db:
            db.delete(db.get(ClassSession, session_id))
            db.commit()
        self.reconcile()
        row = self.runs()[0]
        self.assertEqual((row.state, row.end_reason, row.class_session_id), ("CANCELLED", "SESSION_DELETED", None))

    def test_a_paused_batch_cancels_and_resuming_it_asks_for_approval_again(self):
        self.add_session()
        self.reconcile()
        with self.sf() as db:
            db.get(Batch, self.batch_id).status = BatchStatusEnum.paused
            db.commit()
        self.reconcile()
        self.assertEqual((self.runs()[0].state, self.runs()[0].end_reason), ("CANCELLED", "BATCH_INELIGIBLE"))
        with self.sf() as db:
            row = db.get(ClassSessionRun, self.runs()[0].id)
            row.approval_status = "APPROVED"  # a decision made before the interruption
            db.get(Batch, self.batch_id).status = BatchStatusEnum.active
            db.commit()
        self.reconcile()
        revived = self.runs()[0]
        self.assertEqual((revived.state, revived.approval_status, revived.recording_state),
                         ("SCHEDULED", "PENDING", "AWAITING_APPROVAL"))
        self.assertEqual(len(self.runs()), 1)

    def test_a_batch_without_a_trainer_is_not_scheduled(self):
        self.add_session()
        with self.sf() as db:
            db.get(Batch, self.batch_id).trainer_id = None
            db.commit()
        self.reconcile()
        self.assertEqual(self.runs(), [])

    def test_a_class_already_in_progress_is_untouched_by_schedule_edits(self):
        session_id = self.add_session(minutes_from_now=-5, length=60)
        self.reconcile()
        run_id = self.runs()[0].id
        self.act(lc.activate_run, run_id)
        self.act(lc.mark_trainer_connected, run_id)
        with self.sf() as db:  # someone edits the session while the class is live
            db.get(ClassSession, session_id).start_time = time(3, 0)
            db.commit()
        self.reconcile()
        self.assertEqual(self.run_row(run_id).state, "LIVE")

    def test_lost_time_driven_jobs_are_recreated_after_an_outage(self):
        self.add_session()
        self.reconcile()
        with self.sf() as db:
            for job in db.scalars(select(BackgroundJob)):
                db.delete(job)
            db.commit()
        self.assertEqual(self.jobs(), [])
        self.reconcile()
        self.assertEqual((len(self.jobs("session.activate")), len(self.jobs("run.scheduled_end"))), (1, 1))

    def test_a_pending_grace_termination_survives_a_lost_job(self):
        run_id = self.make_run(state="LIVE")
        t0 = utcnow() - timedelta(seconds=90)
        self.act(lc.mark_trainer_disconnected, run_id, now=t0)
        with self.sf() as db:
            for job in db.scalars(select(BackgroundJob).where(BackgroundJob.kind == "run.grace_expiry")):
                db.delete(job)
            db.commit()
        self.reconcile()
        self.assertEqual(len(self.jobs("run.grace_expiry")), 1)
        self.drain()  # the grace period has long expired: the class ends
        self.assertEqual(self.run_row(run_id).end_reason, "TRAINER_ABSENT_TIMEOUT")

    def test_concurrent_workers_activate_a_class_once(self):
        self.add_session(minutes_from_now=1)
        self.reconcile()
        barrier = threading.Barrier(4)

        def work(name):
            barrier.wait()
            worker.drain(self.sf, name)

        threads = [threading.Thread(target=work, args=(f"w{i}",)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(60)
        self.assertEqual(self.runs()[0].state, "ROOM_OPEN")
        self.assertEqual(len(self.jobs("run.notify_open")), 1)
        with self.sf() as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(RecordingEvent).where(RecordingEvent.event == "room_opened")), 1)


if __name__ == "__main__":
    unittest.main()
