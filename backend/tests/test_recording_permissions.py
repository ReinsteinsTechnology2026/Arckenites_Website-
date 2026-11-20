"""Phase 1 regression tests: who may control, watch and see recordings.

Approved rules under test
  * Batch class: ONLY the assigned trainer approves/controls recording; approval
    starts it automatically; decline never starts it; students can see that a
    class is being recorded; only the trainer and CURRENTLY ENROLLED students
    can watch.
  * General meetings are never recorded - through any API, for any caller.
  * Admins / Super Admins may see status and non-sensitive metadata but cannot
    approve, start, stop, play, download, revoke or delete a recording.
  * Egress is restricted to authorized batch-class rooms.
  * Existing recording rows and audit history are never deleted.

Isolated: temp SQLite, a fake media server, no network, no real credentials.
"""
import importlib.util
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import jwt
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session

from app.config import settings
from app.core import class_runs, recording_pipeline
from app.core.livekit_media import LiveKitMediaServer, room_join_token
from app.core.media_server import (RecordingRefused, require_class_room, room_name_for_run,
                                   set_media_server_factory)
from app.core.rate_limit import limiter
from app.core.security import create_access_token
from app.crud.permissions import DEFAULT_GRANTS, PERMISSION_CATALOG, PERMISSION_KEYS
from app.database import Base, get_db
from app.main import app
from app.models import (AdminRole, AdminRolePermission, AuthAuditLog, AuthSession, Batch, BatchEnrollment,
                        Permission, RoleEnum, User)
from app.models.admin_role import SUPER_ADMIN_SLUG
from app.models.audit_log import AuthEventType
from app.models.class_run import (APPROVAL_APPROVED, APPROVAL_PENDING, APPROVER_TRAINER, REC_AWAITING_APPROVAL,
                                  REC_DECLINED, REC_RECORDING, RUN_ROOM_OPEN, BackgroundJob, ClassRecording,
                                  ClassSessionRun)
from app.models.meeting import (Meeting, MeetingParticipant, MeetingRecording, MeetingStatus, ParticipantRole,
                                ParticipantStatus, RecordingStatus)

BACKEND = Path(__file__).resolve().parents[1]
SENSITIVE_KEYS = {"staging_key", "remote_item_id", "remote_drive_id", "sha256", "egress_id", "file_path", "storage_provider", "stream_url"}


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


class FakeMedia:
    """Records every call; never touches a network."""

    def __init__(self):
        self.rooms, self.started, self.stopped, self.active = [], [], [], {}

    def ensure_room(self, room_name, metadata=""):
        self.rooms.append(room_name)

    def delete_room(self, room_name):
        pass

    def is_participant_present(self, room_name, identity):
        return False

    def find_active_recording(self, room_name):
        return self.active.get(room_name)

    def start_room_recording(self, room_name, staging_key):
        self.started.append((room_name, staging_key))
        self.active[room_name] = f"EG_{len(self.started)}"
        return self.active[room_name]

    def stop_recording(self, egress_id):
        self.stopped.append(egress_id)


class Base_(unittest.TestCase):
    """Shared world: one class (Batch A) with its trainer and students, one
    unrelated class (Batch B), admins of every kind, one general meeting."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="arckenites-recperm-", ignore_cleanup_errors=True)
        self.storage = Path(self.temp.name) / "recordings"
        self.storage.mkdir()
        self.engine = create_engine(f"sqlite:///{Path(self.temp.name) / 'perm.sqlite'}",
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
                _user("student", RoleEnum.student), _user("removed", RoleEnum.student),
                _user("outsider", RoleEnum.student), _user("guest", RoleEnum.student),
                _user("viewer-admin", RoleEnum.admin, admin_role_id=viewer.id),
                _user("super-admin", RoleEnum.admin, admin_role_id=superadmin.id),
                _user("plain-admin", RoleEnum.admin, admin_role_id=plain.id),
                _user("host", RoleEnum.staff),
            ]
            db.add_all(users)
            db.flush()
            self.ids = {u.username: u.id for u in users}
            batch = Batch(name="Batch A", trainer_id=self.ids["trainer"])
            other = Batch(name="Batch B", trainer_id=self.ids["other-trainer"])
            db.add_all([batch, other])
            db.flush()
            self.batch_id = batch.id
            db.add_all([BatchEnrollment(batch_id=batch.id, student_id=self.ids["student"]),
                        BatchEnrollment(batch_id=batch.id, student_id=self.ids["removed"]),
                        BatchEnrollment(batch_id=other.id, student_id=self.ids["outsider"])])
            db.flush()
            now = class_runs.utcnow()
            run = ClassSessionRun(
                occurrence_key="run-a", kind="SCHEDULED", batch_id=batch.id, trainer_id=batch.trainer_id,
                scheduled_start_at=now, scheduled_end_at=now, effective_end_at=now, state=RUN_ROOM_OPEN,
                approval_status=APPROVAL_PENDING, recording_state=REC_AWAITING_APPROVAL)
            db.add(run)
            db.flush()
            self.run_id = run.id
            # a published class recording, plus a legacy general-meeting recording row
            rec = ClassRecording(run_id=run.id, batch_id=batch.id, capture_state="STORED",
                                 publication_state="PUBLISHED", staging_key="incoming/x.mp4", size_bytes=10,
                                 duration_seconds=3, storage_provider="LOCAL_STAGING")
            db.add(rec)
            meeting = Meeting(title="All hands", host_id=self.ids["host"], scheduled_at=now,
                              status=MeetingStatus.LIVE, recording_enabled=True)  # legacy flag ON: must not matter
            db.add(meeting)
            db.flush()
            db.add_all([MeetingParticipant(meeting_id=meeting.id, user_id=self.ids["host"], role=ParticipantRole.HOST,
                                           status=ParticipantStatus.JOINED),
                        MeetingParticipant(meeting_id=meeting.id, user_id=self.ids["guest"],
                                           role=ParticipantRole.PARTICIPANT, status=ParticipantStatus.JOINED)])
            legacy = MeetingRecording(meeting_id=meeting.id, status=RecordingStatus.FAILED, file_path="legacy/old.mp4")
            db.add(legacy)
            db.commit()
            self.rid, self.meeting_id, self.legacy_id = str(rec.id), meeting.id, legacy.id

        self.overrides = app.dependency_overrides.copy()
        app.dependency_overrides[get_db] = self._db
        self.client = TestClient(app)
        self.limiter_enabled = limiter.enabled
        limiter.enabled = False
        self.media = FakeMedia()
        set_media_server_factory(lambda: self.media)
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
        set_media_server_factory(None)
        for p in self.patches:
            p.stop()
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

    def count(self, model, *where):
        with QaSession(self.engine) as db:
            return db.scalar(select(func.count()).select_from(model).where(*where))

    def run_row(self):
        with QaSession(self.engine) as db:
            row = db.get(ClassSessionRun, self.run_id)
            db.expunge(row)
            return row

    def approve(self, who, decision="approve"):
        return self.client.post(f"/api/class-runs/{self.run_id}/recording/approval", headers=self.headers(who),
                                json={"decision": decision, "consent_version": self.consent})

    def status(self, who):
        return self.client.get(f"/api/class-runs/{self.run_id}/recording", headers=self.headers(who))


# ---------------------------------------------------------------------------
# Watching a recording
# ---------------------------------------------------------------------------
class PlaybackAuthorizationTests(Base_):
    def token(self, who):
        return self.client.post(f"/api/recordings/{self.rid}/playback-token", headers=self.headers(who))

    def test_only_the_assigned_trainer_and_enrolled_students_can_watch(self):
        expected = {"student": 200, "removed": 200, "trainer": 200,
                    "outsider": 404, "other-trainer": 404, "guest": 404,
                    "viewer-admin": 404, "super-admin": 404, "plain-admin": 404, "host": 404}
        for who, code in expected.items():
            with self.subTest(who):
                self.assertEqual(self.token(who).status_code, code)
        self.assertEqual(self.client.post(f"/api/recordings/{self.rid}/playback-token").status_code, 401)

    def test_a_student_removed_from_the_batch_can_no_longer_watch(self):
        self.assertEqual(self.token("removed").status_code, 200)
        with QaSession(self.engine) as db:
            db.query(BatchEnrollment).filter(BatchEnrollment.student_id == self.ids["removed"]).delete()
            db.commit()
        self.assertEqual(self.token("removed").status_code, 404)
        self.assertEqual(self.token("student").status_code, 200)  # classmates unaffected

    def test_a_deactivated_student_cannot_watch(self):
        with QaSession(self.engine) as db:
            db.get(User, self.ids["student"]).is_active = False
            db.commit()
        self.assertIn(self.token("student").status_code, (401, 403, 404))

    def test_an_admin_cannot_stream_even_with_a_well_formed_playback_token(self):
        # A playback token only identifies the viewer; the server re-checks standing on every request.
        now = datetime.now(timezone.utc)
        for who in ("super-admin", "viewer-admin"):
            with self.subTest(who):
                forged = jwt.encode({"purpose": "recording-playback", "rid": self.rid, "sub": str(self.ids[who]),
                                     "iat": now, "exp": now + timedelta(minutes=5), "nonce": "n"},
                                    settings.jwt_secret, algorithm="HS256")
                r = self.client.get(f"/api/recordings/{self.rid}/stream", params={"t": forged})
                self.assertEqual(r.status_code, 404)

    def test_there_is_no_admin_download_revoke_or_delete_route_for_class_recordings(self):
        # Class recordings live under /api/recordings and /api/class-runs. (The legacy
        # general-meeting routes under /api/admin/meetings still exist but only refuse;
        # they are covered by GeneralMeetingRecordingTests.)
        class_routes = [r for r in app.routes if getattr(r, "path", "").startswith(("/api/recordings", "/api/class-runs"))]
        self.assertTrue(class_routes)
        for route in class_routes:
            for forbidden in ("download", "revoke", "delete", "remove"):
                self.assertNotIn(forbidden, route.path.lower(), route.path)
            self.assertFalse({"DELETE", "PUT", "PATCH"} & set(route.methods or ()), route.path)


# ---------------------------------------------------------------------------
# Admins: status and non-sensitive metadata only
# ---------------------------------------------------------------------------
class AdminVisibilityTests(Base_):
    def test_admins_with_recordings_view_see_status_but_cannot_control(self):
        for who in ("viewer-admin", "super-admin"):
            with self.subTest(who):
                r = self.status(who)
                self.assertEqual(r.status_code, 200)
                body = r.json()
                self.assertFalse(body["can_control"])
                self.assertFalse(SENSITIVE_KEYS & set(body), "status must not expose storage details")

    def test_an_admin_without_the_view_permission_sees_nothing(self):
        self.assertEqual(self.status("plain-admin").status_code, 404)

    def test_admins_cannot_approve_decline_end_extend_or_leave_for_the_trainer(self):
        for who in ("viewer-admin", "super-admin", "plain-admin"):
            for decision in ("approve", "decline"):
                self.assertIn(self.approve(who, decision).status_code, (403, 404), (who, decision))
            for action in ("end", "trainer-leave"):
                r = self.client.post(f"/api/class-runs/{self.run_id}/{action}", headers=self.headers(who))
                self.assertIn(r.status_code, (403, 404), (who, action))
            r = self.client.post(f"/api/class-runs/{self.run_id}/extend", headers=self.headers(who), json={"minutes": 15})
            self.assertIn(r.status_code, (403, 404, 422), (who, "extend"))
        row = self.run_row()
        self.assertEqual((row.approval_status, row.recording_state), (APPROVAL_PENDING, REC_AWAITING_APPROVAL))
        self.assertEqual(self.count(BackgroundJob), 0)
        self.assertEqual(self.media.started, [])


# ---------------------------------------------------------------------------
# Batch class: approval drives recording
# ---------------------------------------------------------------------------
class ClassApprovalDrivesRecordingTests(Base_):
    def start_job(self, db_session=None):
        with QaSession(self.engine) as db:
            return db.scalar(select(BackgroundJob).where(BackgroundJob.kind == "recording.start"))

    def run_start(self):
        with QaSession(self.engine) as db:
            return recording_pipeline.start_recording(db, self.run_id)

    def test_trainer_approval_schedules_recording_automatically(self):
        self.assertEqual(self.approve("trainer").status_code, 200)
        job = self.start_job()
        self.assertIsNotNone(job, "approval must enqueue the start job with no further click")
        self.assertEqual(json.loads(job.payload), {"run_id": self.run_id})
        self.assertEqual(self.count(BackgroundJob, BackgroundJob.kind == "recording.start"), 1)
        row = self.run_row()
        self.assertEqual((row.approval_status, row.approval_source), (APPROVAL_APPROVED, APPROVER_TRAINER))

    def test_the_scheduled_job_records_exactly_the_class_room(self):
        self.approve("trainer")
        self.assertEqual(self.run_start(), "started")
        self.assertEqual([room for room, _ in self.media.started], [room_name_for_run(self.run_id)])

    def test_trainer_decline_never_starts_recording(self):
        self.assertEqual(self.approve("trainer", "decline").status_code, 200)
        self.assertEqual(self.count(BackgroundJob, BackgroundJob.kind == "recording.start"), 0)
        self.assertEqual(self.run_row().recording_state, REC_DECLINED)
        # even if the job were somehow run, the approval gate holds
        self.assertEqual(self.run_start(), "not_approved")
        self.assertEqual(self.media.started, [])
        self.assertEqual(self.count(ClassRecording, ClassRecording.run_id == self.run_id,
                                    ClassRecording.capture_state == "STARTING"), 0)

    def test_nobody_but_the_assigned_trainer_can_approve(self):
        for who in ("student", "outsider", "other-trainer", "viewer-admin", "super-admin", "plain-admin", "host"):
            with self.subTest(who):
                self.assertIn(self.approve(who).status_code, (403, 404))
        self.assertEqual(self.count(BackgroundJob), 0)
        self.assertEqual(self.run_row().approval_status, APPROVAL_PENDING)

    def test_a_run_marked_approved_by_anything_other_than_the_trainer_is_not_recorded(self):
        with QaSession(self.engine) as db:  # simulate a corrupted / hand-edited row
            run = db.get(ClassSessionRun, self.run_id)
            run.approval_status, run.approval_source = APPROVAL_APPROVED, None
            db.commit()
        self.assertEqual(self.run_start(), "not_approved")
        self.assertEqual(self.media.started, [])

    def test_enrolled_students_can_see_that_the_class_is_being_recorded(self):
        with QaSession(self.engine) as db:
            run = db.get(ClassSessionRun, self.run_id)
            run.approval_status, run.approval_source, run.recording_state = APPROVAL_APPROVED, APPROVER_TRAINER, REC_RECORDING
            db.commit()
        for who in ("student", "removed"):
            body = self.status(who).json()
            self.assertTrue(body["is_recording"], who)
            self.assertEqual(body["recording_state"], REC_RECORDING)
            self.assertFalse(body["can_control"])
        self.assertEqual(self.status("outsider").status_code, 404)  # another batch is told nothing


# ---------------------------------------------------------------------------
# General meetings are never recorded
# ---------------------------------------------------------------------------
class GeneralMeetingRecordingTests(Base_):
    def post(self, path, who=None):
        return self.client.post(path, headers=self.headers(who) if who else {})

    def assert_untouched(self):
        self.assertEqual(self.count(MeetingRecording), 1)
        with QaSession(self.engine) as db:
            row = db.get(MeetingRecording, self.legacy_id)
            self.assertEqual((row.status, row.file_path), (RecordingStatus.FAILED, "legacy/old.mp4"))
        self.assertEqual(self.media.started, [])

    def test_host_and_participants_cannot_start_or_stop_a_recording(self):
        start = f"/api/meetings/{self.meeting_id}/recordings/start"
        stop = f"/api/meetings/{self.meeting_id}/recordings/{self.legacy_id}/stop"
        for who in ("host", "guest", "outsider", "trainer", "super-admin", "plain-admin"):
            with self.subTest(who):
                self.assertEqual(self.post(start, who).status_code, 403)
                self.assertEqual(self.post(stop, who).status_code, 403)
        self.assertEqual(self.post(start).status_code, 401)
        self.assert_untouched()

    def test_the_legacy_recording_enabled_flag_cannot_re_enable_recording(self):
        # the meeting row above has recording_enabled=True, as legacy data might
        self.assertEqual(self.post(f"/api/meetings/{self.meeting_id}/recordings/start", "host").status_code, 403)
        self.assertEqual(self.count(MeetingRecording), 1)

    def test_no_admin_can_start_stop_download_or_delete_a_meeting_recording(self):
        base = f"/api/admin/meetings/{self.meeting_id}/recordings"
        for who in ("super-admin", "viewer-admin", "plain-admin", "host"):
            with self.subTest(who):
                h = self.headers(who)
                self.assertEqual(self.client.post(f"{base}/start", headers=h).status_code, 403)
                self.assertEqual(self.client.post(f"{base}/{self.legacy_id}/stop", headers=h).status_code, 403)
                self.assertEqual(self.client.get(f"{base}/{self.legacy_id}/download", headers=h).status_code, 403)
                self.assertEqual(self.client.delete(f"{base}/{self.legacy_id}", headers=h).status_code, 403)
        self.assertEqual(self.client.post(f"{base}/start").status_code, 401)
        self.assert_untouched()

    def test_an_admin_can_still_list_recording_metadata_but_never_a_storage_path(self):
        r = self.client.get(f"/api/admin/meetings/{self.meeting_id}/recordings", headers=self.headers("super-admin"))
        self.assertEqual(r.status_code, 200)
        rows = r.json()
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0]["file_path"])
        self.assertEqual(rows[0]["status"], "FAILED")

    def test_a_meeting_cannot_be_created_or_edited_with_recording_on(self):
        h = self.headers("super-admin")
        body = {"title": "Standup", "host_id": self.ids["host"], "scheduled_at": "2030-01-01T10:00:00Z"}
        self.assertEqual(self.client.post("/api/admin/meetings", headers=h, json={**body, "recording_enabled": True}).status_code, 403)
        ok = self.client.post("/api/admin/meetings", headers=h, json=body)
        self.assertEqual(ok.status_code, 201)
        self.assertFalse(ok.json()["recording_enabled"])
        mid = ok.json()["id"]
        self.assertEqual(self.client.patch(f"/api/admin/meetings/{mid}", headers=h, json={"recording_enabled": True}).status_code, 403)
        self.assertFalse(self.client.get(f"/api/admin/meetings/{mid}", headers=h).json()["recording_enabled"])
        # ordinary edits still work
        self.assertEqual(self.client.patch(f"/api/admin/meetings/{mid}", headers=h, json={"title": "Renamed"}).status_code, 200)

    def test_deleting_a_meeting_never_deletes_its_recording_records(self):
        with QaSession(self.engine) as db:
            db.get(Meeting, self.meeting_id).status = MeetingStatus.COMPLETED
            db.commit()
        r = self.client.delete(f"/api/admin/meetings/{self.meeting_id}", headers=self.headers("super-admin"))
        self.assertEqual(r.status_code, 409)
        self.assertEqual(self.count(Meeting, Meeting.id == self.meeting_id), 1)
        self.assert_untouched()

    def test_general_meeting_rooms_can_never_be_recorded_by_the_media_layer(self):
        for room in ("meet-1-abcdef", "ArckMeeting-abc123", "class-1-0000000000000000", "", f"{room_name_for_run(1)}x"):
            with self.subTest(room):
                with self.assertRaises(RecordingRefused):
                    require_class_room(room)
        self.assertEqual(require_class_room(room_name_for_run(7)), 7)


# ---------------------------------------------------------------------------
# Egress / LiveKit
# ---------------------------------------------------------------------------
class EgressRestrictionTests(Base_):
    def server(self):
        return LiveKitMediaServer("http://127.0.0.1:1", "APItestkey", "test-livekit-secret-" + "x" * 32,
                                  output_prefix="/out", layout="speaker", preset="H264_720P_30", empty_timeout=60)

    def test_egress_refuses_a_non_class_room_before_building_any_request(self):
        server = self.server()
        with patch.object(LiveKitMediaServer, "_checked", side_effect=AssertionError("must not reach the media server")):
            for room in ("meet-1-abcdef", "general-meeting-9", "class-1-0000000000000000"):
                with self.subTest(room), self.assertRaises(RecordingRefused):
                    server.start_room_recording(room, "incoming/x.mp4")

    def test_egress_accepts_an_authorized_class_room(self):
        server = self.server()
        fake = type("Info", (), {"egress_id": "EG_OK"})()
        with patch.object(LiveKitMediaServer, "_checked", return_value=fake) as call:
            self.assertEqual(server.start_room_recording(room_name_for_run(self.run_id), "incoming/x.mp4"), "EG_OK")
            self.assertEqual(call.call_count, 1)

    def test_join_tokens_never_grant_recording_or_room_management(self):
        room = room_name_for_run(self.run_id)
        for is_trainer in (False, True):
            token = room_join_token(identity="u1", name="Someone", room=room, is_trainer=is_trainer, run_id=self.run_id)
            video = jwt.decode(token, options={"verify_signature": False})["video"]
            with self.subTest(is_trainer=is_trainer):
                self.assertEqual(video["room"], room)
                for grant in ("roomRecord", "roomCreate", "roomList", "ingressAdmin", "hidden", "recorder"):
                    self.assertFalse(video.get(grant), grant)
                self.assertEqual(bool(video.get("roomAdmin")), is_trainer)


# ---------------------------------------------------------------------------
# Permission catalog and migration
# ---------------------------------------------------------------------------
class PermissionCatalogTests(unittest.TestCase):
    def test_recording_management_permissions_are_gone_and_view_is_read_only(self):
        self.assertNotIn("recordings.manage", PERMISSION_KEYS)
        self.assertNotIn("recordings.delete", PERMISSION_KEYS)
        self.assertEqual({k for k in PERMISSION_KEYS if k.startswith("recordings.")}, {"recordings.view"})
        for grants in DEFAULT_GRANTS.values():
            self.assertFalse({"recordings.manage", "recordings.delete"} & set(grants))

    def test_meeting_permissions_no_longer_promise_recording_control(self):
        for entry in PERMISSION_CATALOG:
            if entry["key"].startswith("meetings."):
                self.assertNotIn("recording", entry["description"].lower(), entry["key"])


class PermissionMigrationTests(unittest.TestCase):
    def load(self):
        path = BACKEND / "alembic" / "versions" / "b8d3f6a2c915_recording_permissions_cleanup.py"
        spec = importlib.util.spec_from_file_location("b8d3f6a2c915", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_the_migration_follows_class_videos_and_stays_in_the_single_history(self):
        from alembic.config import Config
        from alembic.script import ScriptDirectory
        module = self.load()
        # In this release the line is: class runs -> class videos -> this cleanup -> private notes.
        # (A separate feedback-sheets line, if shipped later, joins through its own merge revision.)
        self.assertEqual(module.down_revision, "e5f9b2c8d471")
        cfg = Config(str(BACKEND / "alembic.ini"))
        cfg.set_main_option("script_location", str(BACKEND / "alembic"))
        script = ScriptDirectory.from_config(cfg)
        heads = script.get_heads()
        self.assertEqual(len(heads), 1, "the migration history must have exactly one head")
        # later migrations may build on the merge; it must remain in the single line of history
        self.assertIn("b8d3f6a2c915", {r.revision for r in script.iterate_revisions(heads[0], "base")})

    def test_upgrade_retires_the_grants_and_deletes_no_recording_data(self):
        with tempfile.TemporaryDirectory(prefix="arckenites-mig-", ignore_cleanup_errors=True) as tmp:
            engine = create_engine(f"sqlite:///{Path(tmp) / 'm.sqlite'}")
            Base.metadata.create_all(engine)
            with QaSession(engine) as db:
                role = AdminRole(name="Ops", slug="ops")
                db.add(role)
                db.add_all([Permission(key=k, module="x", action="y") for k in
                            ("recordings.view", "recordings.manage", "recordings.delete", "meetings.view")])
                db.flush()
                db.add_all([AdminRolePermission(admin_role_id=role.id, permission_key=k) for k in
                            ("recordings.view", "recordings.manage", "recordings.delete", "meetings.view")])
                host = _user("h", RoleEnum.staff)
                db.add(host)
                db.flush()
                now = datetime.now(timezone.utc)
                meeting = Meeting(title="m", host_id=host.id, scheduled_at=now, recording_enabled=True)
                other = Meeting(title="n", host_id=host.id, scheduled_at=now, recording_enabled=False)
                db.add_all([meeting, other])
                db.flush()
                db.add(MeetingRecording(meeting_id=meeting.id, status=RecordingStatus.AVAILABLE, file_path="a.mp4"))
                batch = Batch(name="B", trainer_id=host.id)
                db.add(batch)
                db.flush()
                run = ClassSessionRun(occurrence_key="k", kind="SCHEDULED", batch_id=batch.id, scheduled_start_at=now,
                                      scheduled_end_at=now, effective_end_at=now)
                db.add(run)
                db.flush()
                db.add(ClassRecording(run_id=run.id, batch_id=batch.id))
                db.add(AuthAuditLog(event_type=AuthEventType.recording_playback, ip_address="127.0.0.1",
                                    user_agent="t", status="success"))
                db.commit()

            def totals():
                with QaSession(engine) as db:
                    return tuple(db.scalar(select(func.count()).select_from(m)) for m in
                                 (MeetingRecording, ClassRecording, AuthAuditLog))

            before = totals()
            with engine.begin() as conn:
                with Operations.context(MigrationContext.configure(conn)):
                    self.load().upgrade()
            with QaSession(engine) as db:
                self.assertEqual(sorted(db.scalars(select(Permission.key)).all()), ["meetings.view", "recordings.view"])
                self.assertEqual(sorted(db.scalars(select(AdminRolePermission.permission_key)).all()),
                                 ["meetings.view", "recordings.view"])
                self.assertEqual(db.scalars(select(Meeting.recording_enabled)).all(), [False, False])
            self.assertEqual(totals(), before, "no recording row or audit row may be deleted")
            with engine.begin() as conn:  # idempotent
                with Operations.context(MigrationContext.configure(conn)):
                    self.load().upgrade()
            self.assertEqual(totals(), before)
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
