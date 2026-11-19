"""Regression tests for the recording reliability fixes found by the first real Egress run:

1. Recording stuck in STARTING when nobody publishes media -> bounded wait, clear notice, retry.
2. participant_left webhook returned HTTP 500 (asyncio.run inside a running event loop).
3. A killed/lost Egress was not noticed until End class -> watchdog marks it FAILED safely.

Rules these tests also pin: consent is still required (retry included), only the assigned trainer can
retry, students/admins never see pipeline details, and general meetings can never be recorded.
"""
import asyncio
import json
import unittest
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from livekit import api
from sqlalchemy import select

from app.config import settings
from app.core import class_lifecycle, class_runs, job_handlers, livekit_media, media_server, recording_pipeline, recording_watchdog
from app.core.class_runs import utcnow
from app.core.livekit_media import LiveKitMediaServer
from app.core.media_server import (EGRESS_FAILED, EGRESS_GONE, EGRESS_MISSING, EGRESS_OK, MediaServerUnavailable,
                                   RecordingRefused, meeting_room_name, require_class_room, room_name_for_run)
from app.models import User
from app.models.class_run import BackgroundJob, ClassRecording, ClassSessionRun, RecordingEvent
from tests.test_job_queue_and_class_lifecycle import FakeMedia, Harness
from tests.test_livekit_integration import event_body, signed


class ProbeMedia(FakeMedia):
    """A fake media server that can say whether anyone is publishing and how an Egress is doing."""

    def __init__(self):
        super().__init__()
        self.published = True
        self.health = {}
        self.health_error = None

    def room_has_published_media(self, room_name):
        return self.published

    def egress_health(self, room_name, egress_id):
        if self.health_error:
            raise self.health_error
        return self.health.get(egress_id, (EGRESS_OK, ""))


class LoopSensitiveMedia(FakeMedia):
    """Behaves like the real server: the presence check spins up its own event loop."""

    def is_participant_present(self, room_name, identity):
        asyncio.run(asyncio.sleep(0))   # raises RuntimeError if called on a thread that is running a loop
        return False


# ---------------------------------------------------------------------------
# 2. participant_left webhook / asyncio.run inside a running loop
# ---------------------------------------------------------------------------
class WebhookEventLoopTests(Harness):
    def setUp(self):
        super().setUp()
        self.run_id = self.make_run(state="LIVE", approval="APPROVED", rec="APPROVED")
        self.room = room_name_for_run(self.run_id)
        self.trainer = f"u{self.ids['trainer']}"

    def post(self, event, **kw):
        body = event_body(event, self.room, event_id=f"EV_{event}_{kw.pop('n', 1)}", **kw)
        return self.client.post("/api/media/webhooks/livekit", content=body.encode(),
                                headers={"Authorization": signed(body), "Content-Type": "application/webhook+json"})

    def test_participant_left_succeeds_when_the_presence_check_needs_its_own_event_loop(self):
        self.media = LoopSensitiveMedia()
        media_server.set_media_server_factory(lambda: self.media)
        self.post("participant_joined", identity=self.trainer, at=utcnow() - timedelta(seconds=20))
        r = self.post("participant_left", identity=self.trainer, n=2)
        self.assertEqual(r.status_code, 200, r.text)           # used to be 500
        self.assertEqual(r.json()["status"], "trainer_disconnected")
        self.assertIsNotNone(self.run_row(self.run_id).trainer_left_at)

    def test_a_failing_handler_still_answers_500_and_lets_livekit_retry(self):
        self.media = LoopSensitiveMedia()
        media_server.set_media_server_factory(lambda: self.media)
        with patch.object(media_server, "run_id_from_room_name", side_effect=RuntimeError("boom")), \
                patch("app.core.media_events.run_id_from_room_name", side_effect=RuntimeError("boom")):
            r = self.post("participant_left", identity=self.trainer)
        self.assertEqual(r.status_code, 500)


class RealServerLoopTests(unittest.TestCase):
    def server(self):
        server = LiveKitMediaServer("http://livekit.test", "key", "secret", output_prefix="/out", layout="speaker",
                                    preset="H264_720P_30", empty_timeout=60)

        class Room:
            async def get_participant(self, request):
                return api.ParticipantInfo(identity=request.identity)

        class Client:
            room = Room()

            async def aclose(self):
                return None

        server._client = lambda: Client()
        return server

    def test_the_sdk_call_works_from_plain_synchronous_code(self):
        self.assertTrue(self.server().is_participant_present("room", "u1"))

    def test_the_sdk_call_works_from_inside_a_running_event_loop(self):
        server = self.server()

        async def inside_a_route():
            return server.is_participant_present("room", "u1")

        self.assertTrue(asyncio.run(inside_a_route()))   # asyncio.run() inside the loop used to raise


class RealServerProbeTests(unittest.TestCase):
    def server(self, participants=None, egress_items=None, participant_present=True):
        server = LiveKitMediaServer("http://livekit.test", "key", "secret", output_prefix="/out", layout="speaker",
                                    preset="H264_720P_30", empty_timeout=60)
        calls = SimpleNamespace(egress_requests=[])

        class Room:
            async def list_participants(self, request):
                return api.ListParticipantsResponse(participants=participants or [])

            async def get_participant(self, request):
                if not participant_present:
                    raise api.TwirpError(code="not_found", msg="gone", status=404)
                return api.ParticipantInfo(identity=request.identity)

        class Egress:
            async def list_egress(self, request):
                calls.egress_requests.append(request)
                return api.ListEgressResponse(items=egress_items or [])

        class Client:
            room, egress = Room(), Egress()

            async def aclose(self):
                return None

        server._client = lambda: Client()
        return server, calls

    @staticmethod
    def person(identity, *sources):
        return api.ParticipantInfo(identity=identity, tracks=[api.TrackInfo(source=s) for s in sources])

    def test_nobody_publishing_is_reported_as_false(self):
        server, _ = self.server(participants=[self.person("u1"), self.person("u2")])
        self.assertIs(server.room_has_published_media("room"), False)

    def test_a_published_camera_mic_or_screen_counts(self):
        for source in (api.TrackSource.CAMERA, api.TrackSource.MICROPHONE, api.TrackSource.SCREEN_SHARE):
            server, _ = self.server(participants=[self.person("u1", source)])
            self.assertIs(server.room_has_published_media("room"), True, source)

    def test_the_recorder_itself_does_not_count_as_published_media(self):
        server, _ = self.server(participants=[self.person("EG_abc", api.TrackSource.CAMERA)])
        self.assertIs(server.room_has_published_media("room"), False)

    def test_an_unreachable_media_server_means_unknown_not_false(self):
        server, _ = self.server()

        def broken(call):
            raise MediaServerUnavailable("down")

        server._checked = broken
        self.assertIsNone(server.room_has_published_media("room"))

    def info(self, status):
        return api.EgressInfo(egress_id="EG_1", status=status)

    def test_egress_health_classification(self):
        active = api.EgressStatus.EGRESS_ACTIVE
        cases = [
            (dict(egress_items=[self.info(active)]), (EGRESS_OK, "")),
            (dict(egress_items=[self.info(active)], participant_present=False), (EGRESS_MISSING, "")),
            (dict(egress_items=[]), (EGRESS_GONE, "egress_not_found")),
            (dict(egress_items=[self.info(api.EgressStatus.EGRESS_ABORTED)]), (EGRESS_FAILED, "egress_aborted")),
            (dict(egress_items=[self.info(api.EgressStatus.EGRESS_FAILED)]), (EGRESS_FAILED, "egress_failed")),
        ]
        for kwargs, expected in cases:
            server, calls = self.server(**kwargs)
            self.assertEqual(server.egress_health("room", "EG_1"), expected, kwargs)
            self.assertEqual(calls.egress_requests[0].egress_id, "EG_1")


# ---------------------------------------------------------------------------
# 1. Recording must not hang in STARTING when nobody publishes media
# ---------------------------------------------------------------------------
class StartWaitsForMediaTests(Harness):
    def setUp(self):
        super().setUp()
        self.media = ProbeMedia()
        media_server.set_media_server_factory(lambda: self.media)
        self.run_id = self.make_run(state="LIVE", approval="APPROVED", rec="APPROVED")
        self.room = room_name_for_run(self.run_id)

    def start(self):
        with self.sf() as db:
            return recording_pipeline.start_recording(db, self.run_id)

    def events(self, name):
        with self.sf() as db:
            return db.scalars(select(RecordingEvent).where(RecordingEvent.event == name)).all()

    def age_recording(self, seconds):
        with self.sf() as db:
            rec = db.scalars(select(ClassRecording)).one()
            rec.created_at = utcnow() - timedelta(seconds=seconds)
            db.commit()

    def status(self, who):
        r = self.client.get(f"/api/class-runs/{self.run_id}/recording", headers=self.headers(who))
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def test_an_empty_stage_does_not_start_egress_and_the_trainer_is_told_it_is_waiting(self):
        self.media.published = False
        self.assertEqual(self.start(), "waiting_for_media")
        self.assertEqual(self.media.started, [])                      # Egress is never started on an empty stage
        rec = self.recordings()[0]
        self.assertEqual((rec.capture_state, rec.egress_id), ("STARTING", None))
        self.assertEqual(self.run_row(self.run_id).recording_state, "STARTING")
        trainer = self.status("trainer")
        self.assertEqual(trainer["notice_code"], "waiting_for_media")
        self.assertIn("camera", trainer["notice"])
        self.assertFalse(trainer["can_retry"])

    def test_waiting_repeatedly_does_not_pile_up_events_or_attempts(self):
        self.media.published = False
        for _ in range(4):
            self.assertEqual(self.start(), "waiting_for_media")
        self.assertEqual(len(self.recordings()), 1)
        self.assertEqual(len(self.events("recording_waiting_for_media")), 1)
        self.assertEqual(len(self.events("recording_start_requested")), 1)

    def test_recording_starts_as_soon_as_media_is_published(self):
        self.media.published = False
        self.start()
        self.media.published = True
        self.assertEqual(self.start(), "started")
        self.assertEqual(len(self.media.started), 1)
        self.assertEqual(len(self.recordings()), 1)

    def test_it_gives_up_after_the_bounded_wait_with_a_clear_failure_and_no_blind_retry(self):
        self.media.published = False
        self.start()
        self.age_recording(settings.recording_start_wait_seconds + 5)
        self.assertEqual(self.start(), "gave_up_no_media")
        rec = self.recordings()[0]
        self.assertEqual((rec.capture_state, rec.error_code), ("FAILED", "no_media_published"))
        self.assertEqual(self.run_row(self.run_id).recording_state, "FAILED")
        self.assertEqual(self.media.started, [])
        self.assertEqual(self.jobs("recording.start"), [])           # no automatic retry: it would fail the same way
        trainer = self.status("trainer")
        self.assertEqual(trainer["notice_code"], "no_media_published")
        self.assertIn("Retry recording", trainer["notice"])
        self.assertTrue(trainer["can_retry"])

    def test_a_wait_that_has_not_expired_yet_keeps_waiting(self):
        self.media.published = False
        self.start()
        self.age_recording(settings.recording_start_wait_seconds - 30)
        self.assertEqual(self.start(), "waiting_for_media")

    def test_a_media_server_that_cannot_answer_keeps_the_previous_start_behaviour(self):
        media_server.set_media_server_factory(lambda: FakeMedia())      # no probe at all
        self.assertEqual(self.start(), "started")

    def test_a_dead_attempts_leftover_egress_is_not_adopted(self):
        with self.sf() as db:
            db.add(ClassRecording(run_id=self.run_id, batch_id=self.batch_id, capture_state="FAILED",
                                  egress_id="EG_OLD", staging_key="incoming/old.mp4", attempt_no=1))
            db.commit()
        self.media.active[self.room] = "EG_OLD"
        self.assertEqual(self.start(), "started")
        self.assertEqual(len(self.media.started), 1)
        self.assertNotEqual(self.recordings()[-1].egress_id, "EG_OLD")
        self.assertEqual(self.recordings()[-1].attempt_no, 2)

    def test_students_and_admins_never_see_waiting_or_failure_details(self):
        self.media.published = False
        self.start()
        self.age_recording(settings.recording_start_wait_seconds + 5)
        self.start()
        student = self.status("student")
        self.assertEqual((student["notice"], student["notice_code"], student["can_retry"]), (None, None, False))
        self.assertNotIn("no_media_published", json.dumps(student))

    def test_the_media_wait_sweeper_requeues_the_start_once_per_window(self):
        self.media.published = False
        self.start()
        now = utcnow()
        with self.sf() as db:
            self.assertEqual(recording_watchdog.check_recordings(db, now)["resumed"], 1)
        with self.sf() as db:
            self.assertEqual(recording_watchdog.check_recordings(db, now)["resumed"], 0)   # same window: deduplicated
        with self.sf() as db:
            self.assertEqual(recording_watchdog.check_recordings(db, now + timedelta(seconds=40))["resumed"], 1)
        self.assertEqual(len([j for j in self.jobs("recording.start")]), 2)


class RetryAfterFailureTests(Harness):
    def setUp(self):
        super().setUp()
        self.media = ProbeMedia()
        media_server.set_media_server_factory(lambda: self.media)
        self.run_id = self.make_run(state="LIVE", approval="APPROVED", rec="APPROVED")
        self.media.published = False
        with self.sf() as db:
            recording_pipeline.start_recording(db, self.run_id)
        with self.sf() as db:
            rec = db.scalars(select(ClassRecording)).one()
            rec.created_at = utcnow() - timedelta(seconds=settings.recording_start_wait_seconds + 5)
            db.commit()
        with self.sf() as db:
            self.assertEqual(recording_pipeline.start_recording(db, self.run_id), "gave_up_no_media")

    def retry(self, who, version=None):
        return self.client.post(f"/api/class-runs/{self.run_id}/recording/approval", headers=self.headers(who),
                                json={"decision": "approve", "consent_version": version or settings.recording_consent_version})

    def test_the_trainer_can_retry_and_a_second_attempt_starts_while_the_first_stays_on_record(self):
        r = self.retry("trainer")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["recording_state"], "APPROVED")
        self.assertEqual(len(self.jobs("recording.start")), 1)
        self.media.published = True
        with self.sf() as db:
            self.assertEqual(recording_pipeline.start_recording(db, self.run_id), "started")
        attempts = self.recordings()
        self.assertEqual([(a.attempt_no, a.capture_state) for a in attempts], [(1, "FAILED"), (2, "STARTING")])
        self.assertEqual(attempts[0].error_code, "no_media_published")

    def test_retry_still_requires_the_current_consent_wording(self):
        r = self.retry("trainer", version="stale-version")
        self.assertEqual(r.status_code, 409)
        self.assertEqual(self.run_row(self.run_id).recording_state, "FAILED")
        self.assertEqual(self.jobs("recording.start"), [])

    def test_only_the_assigned_trainer_can_retry(self):
        for who, allowed in (("student", 403), ("outsider", 404), ("other-trainer", 404)):
            self.assertIn(self.retry(who).status_code, (allowed,), who)
        self.assertEqual(self.run_row(self.run_id).recording_state, "FAILED")
        self.assertEqual(self.jobs("recording.start"), [])

    def test_a_retry_is_refused_once_the_class_has_ended(self):
        with self.sf() as db:
            db.get(ClassSessionRun, self.run_id).state = "ENDED"
            db.commit()
        self.assertEqual(self.retry("trainer").status_code, 409)
        self.assertEqual(self.jobs("recording.start"), [])

    def test_a_double_click_does_not_start_two_recordings(self):
        self.assertEqual(self.retry("trainer").status_code, 200)
        self.assertEqual(self.retry("trainer").status_code, 200)   # second click: already APPROVED again, no new job
        self.assertEqual(len(self.jobs("recording.start")), 1)

    def test_the_retry_path_does_not_apply_to_a_declined_class(self):
        # A decline is not a failure: no retry job is created and no "retry" event is logged for it.
        declined = self.make_run(state="LIVE", approval="DECLINED", rec="DECLINED")
        status = self.client.get(f"/api/class-runs/{declined}/recording", headers=self.headers("trainer")).json()
        self.assertFalse(status["can_retry"])
        self.assertEqual(self.jobs("recording.start"), [])


# ---------------------------------------------------------------------------
# 3. A lost Egress is noticed without waiting for End class
# ---------------------------------------------------------------------------
class WatchdogTests(Harness):
    def setUp(self):
        super().setUp()
        self.media = ProbeMedia()
        media_server.set_media_server_factory(lambda: self.media)
        self.run_id = self.make_run(state="LIVE", approval="APPROVED", rec="RECORDING")
        self.room = room_name_for_run(self.run_id)
        self.t0 = utcnow()
        with self.sf() as db:
            rec = ClassRecording(run_id=self.run_id, batch_id=self.batch_id, capture_state="RECORDING",
                                 egress_id="EG_1", staging_key="incoming/r.mp4", attempt_no=1, started_at=self.t0)
            db.add(rec)
            db.flush()
            db.add(RecordingEvent(run_id=self.run_id, recording_id=rec.id, event="recording_start_accepted"))
            db.commit()

    def sweep(self, at=None):
        with self.sf() as db:
            return recording_watchdog.check_recordings(db, at or self.t0)

    def rec(self):
        return self.recordings()[0]

    def event_names(self):
        with self.sf() as db:
            return [e.event for e in db.scalars(select(RecordingEvent).order_by(RecordingEvent.id)).all()]

    def test_a_missing_recorder_is_debounced_then_failed_after_the_grace_period(self):
        self.media.health["EG_1"] = (EGRESS_MISSING, "")
        self.sweep()
        self.assertEqual(self.rec().capture_state, "RECORDING")          # first sighting: only noted
        self.assertIn("egress_missing", self.event_names())
        self.sweep(self.t0 + timedelta(seconds=10))
        self.assertEqual(self.rec().capture_state, "RECORDING")          # still inside the grace period
        self.sweep(self.t0 + timedelta(seconds=settings.recording_egress_lost_grace_seconds + 5))
        rec = self.rec()
        self.assertEqual((rec.capture_state, rec.error_code), ("FAILED", "egress_lost"))
        self.assertEqual(self.media.stopped, ["EG_1"])                   # best-effort cleanup of the dead recorder

    def test_a_recorder_that_comes_back_in_time_is_not_failed(self):
        self.media.health["EG_1"] = (EGRESS_MISSING, "")
        self.sweep()
        self.media.health["EG_1"] = (EGRESS_OK, "")
        self.sweep(self.t0 + timedelta(seconds=10))
        self.media.health["EG_1"] = (EGRESS_MISSING, "")
        self.sweep(self.t0 + timedelta(seconds=settings.recording_egress_lost_grace_seconds + 60))   # a NEW first sighting
        self.assertEqual(self.rec().capture_state, "RECORDING")
        self.assertIn("egress_recovered", self.event_names())

    def test_a_lost_recorder_while_the_class_runs_queues_one_automatic_retry(self):
        self.media.health["EG_1"] = (EGRESS_GONE, "egress_not_found")
        self.assertEqual(self.sweep()["failed"], 1)
        self.assertEqual(self.rec().error_code, "egress_lost")
        self.assertEqual(self.run_row(self.run_id).recording_state, "APPROVED")      # retrying, class unaffected
        self.assertEqual(self.run_row(self.run_id).state, "LIVE")
        self.assertEqual([j.dedupe_key for j in self.jobs("recording.start")], [f"recording.start:{self.run_id}:attempt2"])

    def test_when_the_retry_budget_is_spent_the_recording_is_reported_failed(self):
        self.media.health["EG_1"] = (EGRESS_GONE, "egress_not_found")
        with patch.object(settings, "recording_max_start_attempts", 1):
            self.sweep()
        self.assertEqual(self.run_row(self.run_id).recording_state, "FAILED")
        self.assertEqual(self.run_row(self.run_id).state, "LIVE")                    # the class itself is untouched
        self.assertEqual(self.jobs("recording.start"), [])

    def test_a_failure_the_webhook_missed_is_picked_up(self):
        self.media.health["EG_1"] = (EGRESS_FAILED, "egress_aborted")
        self.assertEqual(self.sweep()["failed"], 1)
        self.assertEqual(self.rec().error_code, "egress_aborted")

    def test_a_recorder_that_joined_but_never_began_recording_times_out_without_a_blind_retry(self):
        with self.sf() as db:
            rec = db.scalars(select(ClassRecording)).one()
            rec.capture_state = "STARTING"
            db.get(ClassSessionRun, self.run_id).recording_state = "STARTING"
            db.commit()
        self.sweep(self.t0 + timedelta(seconds=5))
        self.assertEqual(self.rec().capture_state, "STARTING")
        self.sweep(self.t0 + timedelta(seconds=settings.recording_activation_timeout_seconds + 5))
        self.assertEqual((self.rec().capture_state, self.rec().error_code), ("FAILED", "start_timeout"))
        self.assertEqual(self.run_row(self.run_id).recording_state, "FAILED")
        self.assertEqual(self.jobs("recording.start"), [])
        trainer = self.client.get(f"/api/class-runs/{self.run_id}/recording", headers=self.headers("trainer")).json()
        self.assertEqual((trainer["notice_code"], trainer["can_retry"]), ("start_timeout", True))

    def test_a_healthy_recording_is_left_alone(self):
        self.assertEqual(self.sweep(self.t0 + timedelta(hours=1))["failed"], 0)
        self.assertEqual(self.rec().capture_state, "RECORDING")
        self.assertEqual(self.media.stopped, [])

    def test_an_unreachable_media_server_changes_nothing(self):
        self.media.health_error = MediaServerUnavailable("down")
        self.sweep(self.t0 + timedelta(hours=1))
        self.assertEqual(self.rec().capture_state, "RECORDING")

    def test_a_class_that_already_ended_is_left_to_the_normal_stop_path(self):
        with self.sf() as db:
            db.get(ClassSessionRun, self.run_id).state = "ENDED"
            db.commit()
        self.media.health["EG_1"] = (EGRESS_GONE, "egress_not_found")
        self.assertEqual(self.sweep()["checked"], 0)
        self.assertEqual(self.rec().capture_state, "RECORDING")

    def test_the_periodic_reconcile_job_runs_the_watchdog_and_survives_it_breaking(self):
        self.media.health["EG_1"] = (EGRESS_GONE, "egress_not_found")
        with self.sf() as db:
            job_handlers._reconcile(db, SimpleNamespace(payload="{}"))
        self.assertEqual(self.rec().capture_state, "FAILED")
        with patch.object(recording_watchdog, "check_recordings", side_effect=RuntimeError("boom")):
            with self.sf() as db:
                job_handlers._reconcile(db, SimpleNamespace(payload="{}"))   # must not raise

    def test_a_failed_recording_after_the_class_ends_does_not_get_a_stop_loop(self):
        self.media.health["EG_1"] = (EGRESS_GONE, "egress_not_found")
        with patch.object(settings, "recording_max_start_attempts", 1):
            self.sweep()
        with self.sf() as db:
            class_lifecycle.trainer_end(db, self.run_id, db.get(User, self.ids["trainer"]), class_lifecycle.END_TRAINER_ENDED)
            db.commit()
        self.assertEqual(self.run_row(self.run_id).recording_state, "FAILED")
        for job in self.jobs("recording.stop"):
            with self.sf() as db:
                recording_pipeline.stop_recording(db, self.run_id)
        self.assertEqual(self.media.stopped.count("EG_1"), 1)               # only the watchdog's one cleanup attempt


# ---------------------------------------------------------------------------
# Rules that must survive these fixes
# ---------------------------------------------------------------------------
class RulesStillHoldTests(Harness):
    def test_general_meeting_rooms_can_never_be_recorded_or_probed_as_class_rooms(self):
        with self.assertRaises(RecordingRefused):
            require_class_room(meeting_room_name(5))
        server = LiveKitMediaServer("http://livekit.test", "key", "secret", output_prefix="/out", layout="speaker",
                                    preset="H264_720P_30", empty_timeout=60)
        with self.assertRaises(RecordingRefused):
            server.start_room_recording(meeting_room_name(5), "incoming/x.mp4")

    def test_the_legacy_meeting_recording_endpoints_still_refuse_everyone(self):
        for who in ("trainer", "student"):
            r = self.client.post("/api/meetings/1/recordings/start", headers=self.headers(who))
            self.assertEqual(r.status_code, 403, who)

    def test_nothing_is_recorded_without_the_trainers_approval_even_if_media_is_published(self):
        media = ProbeMedia()
        media_server.set_media_server_factory(lambda: media)
        run_id = self.make_run(state="LIVE", approval="PENDING", rec="AWAITING_APPROVAL")
        with self.sf() as db:
            self.assertEqual(recording_pipeline.start_recording(db, run_id), "not_approved")
        self.assertEqual((media.started, self.recordings()), ([], []))

    def test_the_watchdog_never_creates_or_approves_anything_on_a_class_that_declined(self):
        run_id = self.make_run(state="LIVE", approval="DECLINED", rec="DECLINED")
        with self.sf() as db:
            self.assertEqual(recording_watchdog.check_recordings(db), {"checked": 0, "resumed": 0, "failed": 0})
        self.assertEqual((self.jobs("recording.start"), self.recordings()), ([], []))
        self.assertEqual(self.run_row(run_id).approval_status, "DECLINED")


if __name__ == "__main__":
    unittest.main()
