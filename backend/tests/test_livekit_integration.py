"""LiveKit integration: join tokens, signed webhooks, presence, recording events.

WHAT THIS PROVES: our authorization logic, signature handling, ordering and
state machine, and that our requests are valid SDK messages. WHAT IT DOES NOT
PROVE: that a real LiveKit server accepts them or that a real Egress writes a
file. Those need the real stack (infra/livekit) and a genuine recording.
"""
import asyncio
import base64
import hashlib
import json
import unittest
from datetime import timedelta
from unittest.mock import patch

import jwt
from livekit import api
from sqlalchemy import select

from app import worker
from app.config import settings
from app.core import class_lifecycle as lc
from app.core import livekit_media, media_events, media_server
from app.core.class_runs import as_utc, utcnow
from app.core.livekit_media import LiveKitMediaServer
from app.core.media_server import (MediaServerNotConfigured, MediaServerUnavailable, room_name_for_run,
                                   run_id_from_room_name)
from app.models import AuthAuditLog, BatchEnrollment, BatchStatusEnum, Batch
from app.models.audit_log import AuthEventType
from app.models.class_run import (BackgroundJob, ClassRecording, ClassSessionRun, MediaWebhookEvent,
                                  RecordingEvent)
from tests.test_job_queue_and_class_lifecycle import Harness

KEY = "APItestkey"


def _secret():
    return settings.livekit_api_secret


def signed(body: str, *, secret=None, key=KEY, ttl=timedelta(minutes=5), sha=None) -> str:
    digest = base64.b64encode(hashlib.sha256(body.encode()).digest()).decode() if sha is None else sha
    return (api.AccessToken(key, secret or _secret()).with_grants(api.VideoGrants())
            .with_ttl(ttl).with_sha256(digest).to_jwt())


def event_body(event, room, *, event_id, at=None, identity=None, reason=None, egress=None, status=None,
               files=None, error=""):
    doc = {"event": event, "id": event_id, "createdAt": int((at or utcnow()).timestamp()), "room": {"name": room}}
    if identity:
        doc["participant"] = {"identity": identity}
        if reason:
            doc["participant"]["disconnectReason"] = reason
    if egress:
        doc["egressInfo"] = {"egressId": egress, "status": status or "EGRESS_ACTIVE", "error": error,
                             "fileResults": files if files is not None else []}
    return json.dumps(doc)


class JoinTokenTests(Harness):
    def join_batch(self, name="trainer", batch_id=None):
        return self.client.post(f"/api/class-runs/batch/{batch_id or self.batch_id}/join", headers=self.headers(name))

    def claims(self, response):
        return jwt.decode(response.json()["token"], _secret(), algorithms=["HS256"], options={"verify_aud": False})

    def test_trainer_joining_with_no_scheduled_class_opens_an_ad_hoc_occurrence_with_a_scoped_token(self):
        r = self.join_batch("trainer")
        self.assertEqual(r.status_code, 200, r.text)
        body, claims = r.json(), self.claims(r)
        with self.sf() as db:
            run = db.scalars(select(ClassSessionRun)).one()
        self.assertEqual((run.kind, run.state, run.approval_status), ("AD_HOC", "ROOM_OPEN", "PENDING"))
        self.assertEqual(body["room_name"], room_name_for_run(run.id))
        self.assertEqual(body["identity"], f"u{self.ids['trainer']}")
        self.assertEqual((claims["iss"], claims["sub"]), (KEY, body["identity"]))
        video = claims["video"]
        self.assertEqual((video["room"], video["roomJoin"], video["roomAdmin"], video["canPublishData"]),
                         (body["room_name"], True, True, True))
        for forbidden in ("roomCreate", "roomList", "roomRecord", "ingressAdmin", "recorder"):
            self.assertFalse(video.get(forbidden), f"token must not grant {forbidden}")
        self.assertLessEqual(claims["exp"] - claims["nbf"], settings.livekit_token_ttl_seconds + 5)
        self.assertEqual(self.media.rooms, [body["room_name"]])
        self.assertEqual(body["recording"]["approval_status"], "PENDING")
        self.assertFalse(body["recording"]["is_recording"])

    def test_the_api_secret_never_appears_in_a_response(self):
        text = self.join_batch("trainer").text
        self.assertNotIn(_secret(), text)

    def test_joining_twice_reuses_the_same_occurrence(self):
        self.join_batch("trainer")
        self.join_batch("trainer")
        self.assertEqual(self.count(ClassSessionRun), 1)

    def test_a_student_cannot_join_until_the_trainer_is_live(self):
        run_id = self.join_batch("trainer").json()["run_id"]
        self.assertEqual(self.join_batch("student").status_code, 409)  # room open, trainer not yet present
        self.act(lc.mark_trainer_connected, run_id)
        r = self.join_batch("student")
        self.assertEqual(r.status_code, 200, r.text)
        video = self.claims(r)["video"]
        self.assertEqual((video["roomAdmin"] if "roomAdmin" in video else False,
                          video.get("canPublishData", False)), (False, False))
        self.assertTrue(video["canPublish"] and video["canSubscribe"])
        self.assertEqual(self.claims(r)["sub"], f"u{self.ids['student']}")

    def test_only_this_batchs_trainer_and_enrolled_students_get_tokens(self):
        run_id = self.join_batch("trainer").json()["run_id"]
        self.act(lc.mark_trainer_connected, run_id)
        self.assertEqual(self.join_batch("outsider").status_code, 403)       # enrolled elsewhere
        self.assertEqual(self.join_batch("other-trainer").status_code, 403)  # trains another batch
        self.assertEqual(self.client.post(f"/api/class-runs/batch/{self.batch_id}/join").status_code, 401)
        self.assertEqual(self.client.post(f"/api/class-runs/batch/999999/join", headers=self.headers("student")).status_code, 404)

    def test_by_run_id_other_batches_get_404_not_a_confirmation_it_exists(self):
        run_id = self.join_batch("trainer").json()["run_id"]
        self.act(lc.mark_trainer_connected, run_id)
        r = self.client.post(f"/api/class-runs/{run_id}/join", headers=self.headers("outsider"))
        self.assertEqual(r.status_code, 404)
        ok = self.client.post(f"/api/class-runs/{run_id}/join", headers=self.headers("student"))
        self.assertEqual(ok.status_code, 200, ok.text)

    def test_a_student_removed_from_the_batch_cannot_get_a_new_token(self):
        run_id = self.join_batch("trainer").json()["run_id"]
        self.act(lc.mark_trainer_connected, run_id)
        self.assertEqual(self.join_batch("student").status_code, 200)
        with self.sf() as db:
            for row in db.scalars(select(BatchEnrollment).where(BatchEnrollment.student_id == self.ids["student"])):
                db.delete(row)
            db.commit()
        self.assertEqual(self.join_batch("student").status_code, 403)

    def test_a_reassigned_trainer_loses_the_right_to_join_as_trainer(self):
        with self.sf() as db:
            db.get(Batch, self.batch_id).trainer_id = self.ids["other-trainer"]
            db.commit()
        self.assertEqual(self.join_batch("trainer").status_code, 403)
        self.assertEqual(self.join_batch("other-trainer").status_code, 200)

    def test_nobody_joins_a_class_that_has_ended_or_a_batch_that_is_not_running(self):
        run_id = self.join_batch("trainer").json()["run_id"]
        self.act(lc.mark_trainer_connected, run_id)
        with self.sf() as db:
            db.get(Batch, self.batch_id).status = BatchStatusEnum.paused
            db.commit()
        self.assertEqual(self.join_batch("student").status_code, 409)
        self.assertEqual(self.join_batch("trainer").status_code, 409)
        with self.sf() as db:
            db.get(Batch, self.batch_id).status = BatchStatusEnum.active
            db.get(ClassSessionRun, run_id).state = "ENDED"
            db.commit()
        r = self.client.post(f"/api/class-runs/{run_id}/join", headers=self.headers("trainer"))
        self.assertEqual(r.status_code, 409)

    def test_a_trainer_arriving_shortly_before_a_scheduled_class_starts_that_class_not_an_ad_hoc_one(self):
        self.add_session(minutes_from_now=20)
        self.act(lc.reconcile_sessions)
        r = self.join_batch("trainer")
        self.assertEqual(r.status_code, 200, r.text)
        with self.sf() as db:
            run = db.scalars(select(ClassSessionRun)).one()
        self.assertEqual((run.kind, run.state, run.id), ("SCHEDULED", "ROOM_OPEN", r.json()["run_id"]))

    def test_media_server_trouble_is_reported_as_unavailable(self):
        self.media.fail_with = MediaServerUnavailable("down")
        self.assertEqual(self.join_batch("trainer").status_code, 503)
        self.media.fail_with = None
        with patch.object(settings, "livekit_api_secret", ""):
            r = self.join_batch("trainer")
        self.assertEqual(r.status_code, 503)

    def test_a_token_request_does_not_create_attendance_or_start_recording(self):
        self.join_batch("trainer")
        self.assertEqual(self.count(ClassRecording), 0)
        self.assertEqual(self.jobs("recording.start"), [])


class WebhookTests(Harness):
    def setUp(self):
        super().setUp()
        self.run_id = self.make_run(state="ROOM_OPEN")
        self.room = room_name_for_run(self.run_id)
        self.trainer = f"u{self.ids['trainer']}"
        self.n = 0

    def post(self, body, token=None, **kw):
        headers = {"Authorization": token if token is not None else signed(body), "Content-Type": "application/webhook+json"}
        headers.update(kw.get("headers", {}))
        return self.client.post("/api/media/webhooks/livekit", content=body.encode(), headers=headers)

    def ev(self, event, **kw):
        self.n += 1
        kw.setdefault("event_id", f"EV_{self.n}_{event}")
        return event_body(event, kw.pop("room", self.room), **kw)

    # -- signature validation -------------------------------------------------
    def test_a_correctly_signed_event_is_applied(self):
        body = self.ev("participant_joined", identity=self.trainer)
        r = self.post(body)
        self.assertEqual((r.status_code, r.json()["status"]), (200, "trainer_connected"))
        self.assertEqual(self.run_row(self.run_id).state, "LIVE")

    def test_invalid_signatures_are_rejected_and_change_nothing(self):
        body = self.ev("participant_joined", identity=self.trainer)
        other_body = self.ev("participant_joined", identity=self.trainer, event_id="EV_other")
        cases = {
            "wrong secret": signed(body, secret="a-different-secret-" + "y" * 32),
            "wrong key": signed(body, key="OTHERKEY"),
            "token for a different body (tampered)": signed(other_body),
            "expired token": signed(body, ttl=timedelta(hours=-2)),
            "garbage token": "not.a.jwt",
            "no sha256 claim": api.AccessToken(KEY, _secret()).with_grants(api.VideoGrants()).to_jwt(),
            "empty": "",
        }
        for name, token in cases.items():
            with self.subTest(name):
                r = self.post(body, token=token)
                self.assertEqual(r.status_code, 401, r.text)
        r = self.client.post("/api/media/webhooks/livekit", content=body.encode())  # no Authorization header at all
        self.assertEqual(r.status_code, 401)
        self.assertEqual(self.run_row(self.run_id).state, "ROOM_OPEN")
        self.assertEqual(self.count(MediaWebhookEvent), 0)

    def test_a_bearer_prefix_is_accepted_but_the_signature_is_still_checked(self):
        body = self.ev("participant_joined", identity=self.trainer)
        self.assertEqual(self.post(body, token="Bearer " + signed(body)).status_code, 200)
        other = self.ev("participant_joined", identity=self.trainer)
        self.assertEqual(self.post(other, token="Bearer " + signed(body)).status_code, 401)

    def test_unconfigured_media_server_rejects_webhooks(self):
        body = self.ev("participant_joined", identity=self.trainer)
        with patch.object(settings, "livekit_api_secret", ""):
            self.assertEqual(self.post(body, token="x").status_code, 503)

    def test_oversized_bodies_are_refused(self):
        self.assertEqual(self.post("x" * (300 * 1024), token="x").status_code, 413)

    # -- replay / duplicates ----------------------------------------------------
    def test_a_replayed_event_is_acknowledged_but_not_applied_twice(self):
        body = self.ev("participant_left", identity=self.trainer, event_id="EV_same")
        self.act(lc.mark_trainer_connected, self.run_id, event_at=utcnow() - timedelta(seconds=30))
        self.assertEqual(self.post(body).json()["status"], "trainer_disconnected")
        self.assertEqual(self.post(body).json()["status"], "duplicate")
        self.assertEqual(self.post(body).json()["status"], "duplicate")
        self.assertEqual(len(self.jobs("run.grace_expiry")), 1)
        self.assertEqual(self.count(MediaWebhookEvent), 1)

    def test_a_failed_delivery_is_retried_not_swallowed_as_a_duplicate(self):
        body = self.ev("participant_joined", identity=self.trainer, event_id="EV_flaky")
        with patch.object(media_events, "handle_event", side_effect=RuntimeError("db hiccup")):
            self.assertEqual(self.post(body).status_code, 500)
        self.assertEqual(self.count(MediaWebhookEvent), 0)  # the event id was rolled back with the failure
        self.assertEqual(self.post(body).json()["status"], "trainer_connected")  # LiveKit's retry now succeeds
        self.assertEqual(self.run_row(self.run_id).state, "LIVE")

    # -- which events count -----------------------------------------------------
    def test_unknown_forged_and_foreign_rooms_are_ignored(self):
        forged = f"class-{self.run_id}-{'0' * 16}"  # right id, wrong HMAC
        for room in ("some-other-room", forged, "class-999999-" + "a" * 16):
            with self.subTest(room):
                r = self.post(self.ev("participant_joined", room=room, identity=self.trainer))
                self.assertEqual(r.json()["status"], "ignored_unknown_room")
        self.assertEqual(self.run_row(self.run_id).state, "ROOM_OPEN")

    def test_room_names_round_trip_and_cannot_be_forged(self):
        self.assertEqual(run_id_from_room_name(self.room), self.run_id)
        self.assertIsNone(run_id_from_room_name(self.room[:-1] + ("0" if self.room[-1] != "0" else "1")))
        self.assertIsNone(run_id_from_room_name("class-1-xyz"))
        self.assertIsNone(run_id_from_room_name(""))

    def test_only_the_batch_trainer_drives_class_presence(self):
        student = f"u{self.ids['student']}"
        impostor = f"u{self.ids['other-trainer']}"
        for who in (student, impostor, "u999999", "someone"):
            r = self.post(self.ev("participant_joined", identity=who))
            self.assertEqual(r.json()["status"], "ignored_not_trainer")
        self.assertEqual(self.run_row(self.run_id).state, "ROOM_OPEN")

    # -- ordering ---------------------------------------------------------------
    def test_a_late_left_event_older_than_a_rejoin_is_ignored(self):
        base = utcnow() - timedelta(seconds=30)
        self.post(self.ev("participant_joined", identity=self.trainer, at=base + timedelta(seconds=10)))
        r = self.post(self.ev("participant_left", identity=self.trainer, at=base + timedelta(seconds=5)))
        self.assertEqual(r.json()["status"], "ignored_stale")
        self.assertEqual(self.run_row(self.run_id).trainer_left_at, None)
        self.assertEqual(self.jobs("run.grace_expiry"), [])

    def test_a_same_second_left_then_joined_leaves_the_trainer_present(self):
        at = utcnow() - timedelta(seconds=20)
        self.post(self.ev("participant_joined", identity=self.trainer, at=at - timedelta(seconds=30)))
        self.post(self.ev("participant_left", identity=self.trainer, at=at))
        self.assertIsNotNone(self.run_row(self.run_id).trainer_left_at)
        r = self.post(self.ev("participant_joined", identity=self.trainer, at=at))  # same second as the drop
        self.assertEqual(r.json()["status"], "trainer_connected")
        self.assertIsNone(self.run_row(self.run_id).trainer_left_at)

    def test_a_tab_refresh_does_not_start_the_termination_clock(self):
        self.post(self.ev("participant_joined", identity=self.trainer, at=utcnow() - timedelta(seconds=20)))
        r = self.post(self.ev("participant_left", identity=self.trainer, reason="DUPLICATE_IDENTITY"))
        self.assertEqual(r.json()["status"], "ignored_duplicate_identity")
        self.assertIsNone(self.run_row(self.run_id).trainer_left_at)

    def test_a_left_event_is_double_checked_against_the_media_server(self):
        self.post(self.ev("participant_joined", identity=self.trainer, at=utcnow() - timedelta(seconds=20)))
        self.media.present[(self.room, self.trainer)] = True  # the trainer is in fact still connected
        r = self.post(self.ev("participant_left", identity=self.trainer))
        self.assertEqual(r.json()["status"], "ignored_still_present")
        self.assertIsNone(self.run_row(self.run_id).trainer_left_at)

    def test_if_the_media_server_cannot_confirm_the_grace_period_is_the_safety_net(self):
        self.post(self.ev("participant_joined", identity=self.trainer, at=utcnow() - timedelta(seconds=20)))
        self.media.is_participant_present = lambda room, identity: (_ for _ in ()).throw(MediaServerUnavailable("down"))
        r = self.post(self.ev("participant_left", identity=self.trainer))
        self.assertEqual(r.json()["status"], "trainer_disconnected")
        self.assertEqual(self.run_row(self.run_id).state, "LIVE")  # not ended yet: the grace period decides

    def test_old_presence_events_are_ignored(self):
        old = utcnow() - timedelta(seconds=settings.media_webhook_presence_max_age_seconds + 60)
        r = self.post(self.ev("participant_joined", identity=self.trainer, at=old))
        self.assertEqual(r.json()["status"], "ignored_stale")

    # -- trainer departure, end to end ------------------------------------------
    def test_trainer_drop_then_no_return_ends_the_class_and_finalizes_the_recording(self):
        self.post(self.ev("participant_joined", identity=self.trainer, at=utcnow() - timedelta(seconds=90)))
        with self.sf() as db:
            run = db.get(ClassSessionRun, self.run_id)
            run.approval_status, run.recording_state = "APPROVED", "RECORDING"
            db.add(ClassRecording(run_id=self.run_id, batch_id=self.batch_id, capture_state="RECORDING",
                                  egress_id="EG_LIVE", staging_key="incoming/a.mp4"))
            db.commit()
        self.post(self.ev("participant_left", identity=self.trainer))
        grace = self.jobs("run.grace_expiry")[0]
        self.assertEqual(self.run_row(self.run_id).state, "LIVE")
        with self.sf() as db:  # the grace period elapses without a rejoin
            db.get(BackgroundJob, grace.id).run_at = utcnow() - timedelta(seconds=1)
            payload = json.loads(db.get(BackgroundJob, grace.id).payload)
            payload["left_at"] = (utcnow() - timedelta(seconds=61)).isoformat()
            db.get(ClassSessionRun, self.run_id).trainer_left_at = utcnow() - timedelta(seconds=61)
            db.get(BackgroundJob, grace.id).payload = json.dumps(payload)
            db.commit()
        worker.drain(self.sf, "w1")
        row = self.run_row(self.run_id)
        self.assertEqual((row.state, row.end_reason, row.recording_state), ("ENDED", "TRAINER_ABSENT_TIMEOUT", "STOPPING"))
        self.assertEqual(self.media.stopped, ["EG_LIVE"])        # the recording was stopped
        self.assertEqual(self.media.deleted, [self.room])        # and everyone removed from the room

    def test_rejoin_inside_the_grace_period_keeps_the_class_and_recording(self):
        self.post(self.ev("participant_joined", identity=self.trainer, at=utcnow() - timedelta(seconds=40)))
        self.post(self.ev("participant_left", identity=self.trainer, at=utcnow() - timedelta(seconds=20)))
        self.post(self.ev("participant_joined", identity=self.trainer, at=utcnow()))
        row = self.run_row(self.run_id)
        self.assertEqual((row.state, row.trainer_left_at), ("LIVE", None))
        grace = self.jobs("run.grace_expiry")[0]
        with self.sf() as db:
            db.get(BackgroundJob, grace.id).run_at = utcnow() - timedelta(seconds=1)
            db.commit()
        worker.drain(self.sf, "w1")
        self.assertEqual(self.run_row(self.run_id).state, "LIVE")


class EgressEventTests(Harness):
    def setUp(self):
        super().setUp()
        self.run_id = self.make_run(state="LIVE", approval="APPROVED", rec="STARTING")
        self.room = room_name_for_run(self.run_id)
        with self.sf() as db:
            rec = ClassRecording(run_id=self.run_id, batch_id=self.batch_id, capture_state="STARTING",
                                 egress_id="EG_SETUP", staging_key="incoming/r.mp4")
            db.add(rec)
            db.commit()
        self.n = 0

    def post(self, status, egress="EG_SETUP", files=None, error="", event="egress_updated", at=None):
        self.n += 1
        body = event_body(event, self.room, event_id=f"EG{self.n}", egress=egress, status=status, files=files, error=error)
        return self.client.post("/api/media/webhooks/livekit", content=body.encode(),
                                headers={"Authorization": signed(body), "Content-Type": "application/webhook+json"})

    def rec(self):
        return self.recordings()[-1]

    FILE = [{"filename": "/out/incoming/r.mp4", "size": "4096", "duration": "12000000000"}]

    def test_recording_is_only_reported_once_the_media_server_says_it_is_active(self):
        self.assertEqual(self.run_row(self.run_id).recording_state, "STARTING")
        self.assertEqual(self.post("EGRESS_ACTIVE").json()["status"], "recording_active")
        self.assertEqual((self.rec().capture_state, self.run_row(self.run_id).recording_state), ("RECORDING", "RECORDING"))
        self.assertEqual(self.count(AuthAuditLog, AuthAuditLog.event_type == AuthEventType.recording_started), 1)

    def test_a_repeated_active_event_is_a_no_op(self):
        self.post("EGRESS_ACTIVE")
        self.assertEqual(self.post("EGRESS_ACTIVE").json()["status"], "ignored_not_forward")
        self.assertEqual(self.count(AuthAuditLog, AuthAuditLog.event_type == AuthEventType.recording_started), 1)

    def test_completion_moves_to_finalizing_and_queues_verification_once(self):
        self.post("EGRESS_ACTIVE")
        r = self.post("EGRESS_COMPLETE", files=self.FILE, event="egress_ended")
        self.assertEqual(r.json()["status"], "recording_complete")
        rec = self.rec()
        self.assertEqual(rec.capture_state, "FINALIZING")  # NOT stored: the file has not been verified
        self.assertEqual(self.run_row(self.run_id).recording_state, "PROCESSING")
        self.assertEqual(json.loads(rec.media_info)["reported"]["size"], 4096)
        self.assertEqual(self.post("EGRESS_COMPLETE", files=self.FILE, event="egress_ended").json()["status"], "ignored_not_forward")
        self.assertEqual(len(self.jobs("recording.finalize")), 1)

    def test_completion_arriving_before_active_still_works_and_a_late_active_is_ignored(self):
        self.assertEqual(self.post("EGRESS_COMPLETE", files=self.FILE, event="egress_ended").json()["status"], "recording_complete")
        self.assertEqual(self.post("EGRESS_ACTIVE").json()["status"], "ignored_not_forward")
        self.assertEqual(self.rec().capture_state, "FINALIZING")
        self.assertEqual(self.run_row(self.run_id).recording_state, "PROCESSING")

    def test_completion_without_a_file_is_a_failure_not_a_success(self):
        r = self.post("EGRESS_COMPLETE", files=[], event="egress_ended")
        self.assertEqual(self.rec().error_code, "no_file_reported")
        self.assertEqual(self.rec().capture_state, "FAILED")
        self.assertEqual(self.jobs("recording.finalize"), [])

    def test_a_failure_while_the_class_is_running_is_retried_once_then_reported(self):
        self.post("EGRESS_ACTIVE")
        r = self.post("EGRESS_FAILED", error="encoder crashed", event="egress_ended")
        self.assertEqual(r.json()["status"], "recording_failed_retrying")
        first = self.recordings()[0]
        self.assertEqual((first.capture_state, first.error_code, first.retry_count), ("FAILED", "egress_failed", 1))
        self.assertIn("encoder crashed", first.error_detail)
        self.assertEqual(self.run_row(self.run_id).recording_state, "APPROVED")  # not "failed" while a retry is queued
        retry = self.jobs("recording.start")
        self.assertEqual([j.dedupe_key for j in retry], [f"recording.start:{self.run_id}:attempt2"])
        worker.drain(self.sf, "w1")  # the retry starts a second attempt through the normal approval-gated path
        rows = self.recordings()
        self.assertEqual([(r_.attempt_no, r_.capture_state) for r_ in rows], [(1, "FAILED"), (2, "STARTING")])
        self.assertEqual(len(self.media.started), 1)
        # the second attempt fails too: now it is reported, with no endless retries
        with self.sf() as db:
            db.get(ClassRecording, rows[1].id).egress_id = "EG_2"
            db.commit()
        self.post("EGRESS_FAILED", egress="EG_2", error="again", event="egress_ended")
        self.assertEqual(self.run_row(self.run_id).recording_state, "FAILED")
        self.assertEqual(len(self.jobs("recording.start")), 1)  # still only the one retry: no attempt 3
        self.assertEqual(self.count(AuthAuditLog, AuthAuditLog.event_type == AuthEventType.recording_failed), 2)

    def test_a_failure_after_the_class_ended_is_not_retried(self):
        with self.sf() as db:
            db.get(ClassSessionRun, self.run_id).state = "ENDED"
            db.commit()
        self.assertEqual(self.post("EGRESS_ABORTED", event="egress_ended").json()["status"], "recording_failed")
        self.assertEqual(self.run_row(self.run_id).recording_state, "FAILED")
        self.assertEqual(self.jobs("recording.start"), [])

    def test_every_failure_status_is_a_failure(self):
        for status in ("EGRESS_FAILED", "EGRESS_ABORTED", "EGRESS_LIMIT_REACHED"):
            with self.subTest(status):
                with self.sf() as db:
                    db.get(ClassSessionRun, self.run_id).state = "ENDED"
                    for rec in db.scalars(select(ClassRecording)):
                        rec.capture_state, rec.egress_id = "FAILED", None
                    db.add(ClassRecording(run_id=self.run_id, batch_id=self.batch_id, capture_state="RECORDING",
                                          egress_id=f"EG_{status}", staging_key=f"incoming/{status}.mp4", attempt_no=9))
                    db.commit()
                self.post(status, egress=f"EG_{status}", event="egress_ended")
                self.assertEqual(self.recordings()[-1].capture_state, "FAILED")

    def test_events_for_recordings_we_did_not_start_are_ignored(self):
        self.assertEqual(self.post("EGRESS_ACTIVE", egress="EG_STRANGER").json()["status"], "ignored_unknown_egress")

    def test_full_recording_lifecycle_through_approval_stop_and_media_events(self):
        run_id = self.make_run(state="ROOM_OPEN")
        room = room_name_for_run(run_id)
        trainer = self.headers("trainer")
        self.client.post(f"/api/class-runs/{run_id}/recording/prompt", headers=trainer)
        r = self.client.post(f"/api/class-runs/{run_id}/recording/approval", headers=trainer,
                             json={"decision": "approve", "consent_version": settings.recording_consent_version})
        self.assertEqual(r.status_code, 200, r.text)
        worker.drain(self.sf, "w1")                         # the approval-gated start job runs
        rec = [x for x in self.recordings() if x.run_id == run_id][0]
        self.assertEqual((rec.capture_state, rec.egress_id), ("STARTING", "EG_1"))
        self.assertEqual(self.media.started[0][0], room)

        def hook(status, event, files=None, egress="EG_1"):
            self.n += 1
            body = event_body(event, room, event_id=f"LC{self.n}", egress=egress, status=status, files=files)
            return self.client.post("/api/media/webhooks/livekit", content=body.encode(),
                                    headers={"Authorization": signed(body)})

        hook("EGRESS_ACTIVE", "egress_started")
        self.assertEqual(self.run_row(run_id).recording_state, "RECORDING")
        self.client.post(f"/api/class-runs/{run_id}/end", headers=trainer)   # the trainer ends the class
        worker.drain(self.sf, "w1")
        self.assertEqual(self.media.stopped, ["EG_1"])
        hook("EGRESS_COMPLETE", "egress_ended", files=self.FILE)
        rec = [x for x in self.recordings() if x.run_id == run_id][0]
        self.assertEqual(rec.capture_state, "FINALIZING")
        self.assertEqual(len(self.jobs("recording.finalize")), 1)
        self.assertEqual(self.run_row(run_id).state, "ENDED")


class LiveKitClientTests(unittest.TestCase):
    """The LiveKit client builds valid SDK requests and maps errors safely.
    These use a fake SDK client; see the module docstring for what that does
    and does not prove."""

    class FakeApi:
        def __init__(self, behaviour=None):
            self.calls, self.behaviour = [], behaviour or {}
            outer = self

            class Room:
                async def create_room(_, req):
                    return outer._do("create_room", req)

                async def delete_room(_, req):
                    return outer._do("delete_room", req)

                async def get_participant(_, req):
                    return outer._do("get_participant", req)

            class Egress:
                async def start_room_composite_egress(_, req):
                    return outer._do("start", req)

                async def stop_egress(_, req):
                    return outer._do("stop", req)

                async def list_egress(_, req):
                    return outer._do("list", req)

            self.room, self.egress = Room(), Egress()

        def _do(self, name, req):
            self.calls.append((name, req))
            result = self.behaviour.get(name)
            if isinstance(result, BaseException):
                raise result
            return result

        async def aclose(self):
            pass

    def server(self, fake):
        s = LiveKitMediaServer("http://lk.test", KEY, "s" * 40, output_prefix="/out/", layout="speaker",
                               preset="H264_720P_30", empty_timeout=600)
        s._client = lambda: fake
        return s

    def test_start_builds_a_valid_room_composite_mp4_request(self):
        fake = self.FakeApi({"start": api.EgressInfo(egress_id="EG_7")})
        self.assertEqual(self.server(fake).start_room_recording("class-1-abc", "incoming/x.mp4"), "EG_7")
        req = fake.calls[0][1]
        self.assertEqual((req.room_name, req.layout, req.preset), ("class-1-abc", "speaker", api.EncodingOptionsPreset.H264_720P_30))
        out = req.file_outputs[0]
        self.assertEqual((out.file_type, out.filepath), (api.EncodedFileType.MP4, "/out/incoming/x.mp4"))

    def test_start_without_an_egress_id_is_an_error_not_a_success(self):
        with self.assertRaises(MediaServerUnavailable):
            self.server(self.FakeApi({"start": api.EgressInfo()})).start_room_recording("r", "incoming/x.mp4")

    def test_find_active_recording_only_returns_a_running_egress(self):
        resp = api.ListEgressResponse(items=[api.EgressInfo(egress_id="DONE", status=api.EgressStatus.EGRESS_COMPLETE),
                                              api.EgressInfo(egress_id="LIVE", status=api.EgressStatus.EGRESS_ACTIVE)])
        fake = self.FakeApi({"list": resp})
        self.assertEqual(self.server(fake).find_active_recording("r"), "LIVE")
        self.assertTrue(fake.calls[0][1].active)
        self.assertIsNone(self.server(self.FakeApi({"list": api.ListEgressResponse()})).find_active_recording("r"))

    def test_stop_and_delete_are_idempotent_when_the_thing_is_already_gone(self):
        gone = api.TwirpError("not_found", "x", status=404)
        s = self.server(self.FakeApi({"stop": gone, "delete_room": gone}))
        s.stop_recording("EG"); s.delete_room("r")  # no exception
        failed = api.TwirpError("failed_precondition", "already ended", status=412)
        self.server(self.FakeApi({"stop": failed})).stop_recording("EG")

    def test_ensure_room_tolerates_an_existing_room_and_sets_the_empty_timeout(self):
        fake = self.FakeApi({"create_room": api.TwirpError("already_exists", "x", status=409)})
        self.server(fake).ensure_room("r", "{}")
        self.assertEqual(fake.calls[0][1].empty_timeout, 600)

    def test_other_errors_become_media_unavailable_without_leaking_secrets(self):
        s = self.server(self.FakeApi({"stop": api.TwirpError("unauthenticated", "bad token SECRETVALUE", status=401)}))
        with self.assertRaises(MediaServerUnavailable) as ctx:
            s.stop_recording("EG")
        self.assertNotIn("SECRETVALUE", str(ctx.exception))
        self.assertNotIn("s" * 40, str(ctx.exception))

    def test_connection_failures_become_media_unavailable(self):
        import aiohttp
        s = self.server(self.FakeApi({"delete_room": aiohttp.ClientConnectionError("refused")}))
        with self.assertRaises(MediaServerUnavailable):
            s.delete_room("r")

    def test_participant_presence_is_true_false_or_unknown(self):
        self.assertTrue(self.server(self.FakeApi({"get_participant": api.ParticipantInfo(identity="u1")})).is_participant_present("r", "u1"))
        self.assertFalse(self.server(self.FakeApi({"get_participant": api.TwirpError("not_found", "x", status=404)})).is_participant_present("r", "u1"))
        self.assertIsNone(self.server(self.FakeApi({"get_participant": api.TwirpError("internal", "x", status=500)})).is_participant_present("r", "u1"))

    def test_get_media_server_requires_configuration(self):
        media_server.set_media_server_factory(None)
        with patch.object(settings, "livekit_url", ""), patch.object(settings, "livekit_api_key", ""):
            with self.assertRaises(MediaServerNotConfigured):
                media_server.get_media_server()
        with patch.object(settings, "livekit_url", "wss://media.example.test"), \
                patch.object(settings, "livekit_api_key", KEY), patch.object(settings, "livekit_api_secret", "z" * 40):
            self.assertIsInstance(media_server.get_media_server(), LiveKitMediaServer)
            self.assertEqual(settings.livekit_server_api_url, "https://media.example.test")

    def test_tokens_require_configuration(self):
        with patch.object(settings, "livekit_api_secret", ""):
            with self.assertRaises(MediaServerNotConfigured):
                livekit_media.room_join_token(identity="u1", name="n", room="r", is_trainer=False, run_id=1)


if __name__ == "__main__":
    unittest.main()
