"""Raise hand, trainer moderation, LiveKit general meetings and admin exclusion.

Isolated: temp SQLite and a fake media server; no network, no real credentials.
"""
import unittest
from unittest.mock import patch

import jwt
from sqlalchemy import select

from app.config import settings
from app.core.livekit_media import LiveKitMediaServer, meeting_join_token
from app.core.media_server import (MediaServerUnavailable, RecordingRefused, meeting_id_from_room_name,
                                   meeting_room_name, require_class_room, room_name_for_run,
                                   set_media_server_factory)
from app.models import BatchEnrollment
from app.models.class_run import RUN_ENDED, ClassRecording, ClassSessionRun
from app.models.meeting import Meeting, ParticipantStatus, MeetingParticipant
from test_recording_permissions import Base_, FakeMedia, QaSession

NON_STUDENTS = ["trainer", "other-trainer", "outsider", "guest", "host", "viewer-admin", "super-admin", "plain-admin"]


class ActionMedia(FakeMedia):
    def __init__(self):
        super().__init__()
        self.attrs, self.muted, self.removed, self.down = [], [], [], False

    def _check(self):
        if self.down:
            raise MediaServerUnavailable("down")

    def set_participant_attributes(self, room_name, identity, attributes):
        self._check()
        self.attrs.append((room_name, identity, dict(attributes)))

    def mute_participant_microphone(self, room_name, identity):
        self._check()
        self.muted.append((room_name, identity))

    def remove_participant(self, room_name, identity):
        self._check()
        self.removed.append((room_name, identity))


class ActionBase(Base_):
    def setUp(self):
        super().setUp()
        self.media = ActionMedia()
        set_media_server_factory(lambda: self.media)
        self.room = room_name_for_run(self.run_id)

    def identity(self, who):
        return f"u{self.ids[who]}"

    def hand(self, who, raised=True, **extra):
        return self.client.post(f"/api/class-runs/{self.run_id}/hand", headers=self.headers(who),
                                json={"raised": raised, **extra})

    def act(self, who, action, identity):
        return self.client.post(f"/api/class-runs/{self.run_id}/moderation/{action}", headers=self.headers(who),
                                json={"identity": identity})

    def untouched(self):
        self.assertEqual((self.media.attrs, self.media.muted, self.media.removed), ([], [], []))


class RaiseHandTests(ActionBase):
    def test_an_enrolled_student_raises_and_lowers_only_their_own_hand_in_the_class_room(self):
        self.assertEqual(self.hand("student").status_code, 204)
        self.assertEqual(self.hand("student", False).status_code, 204)
        self.assertEqual(self.media.attrs, [(self.room, self.identity("student"), {"hand": "1"}),
                                            (self.room, self.identity("student"), {"hand": ""})])

    def test_a_body_naming_someone_else_cannot_redirect_the_hand(self):
        self.assertEqual(self.hand("student", identity=self.identity("removed"), room="other", user_id=1).status_code, 204)
        self.assertEqual([a[1] for a in self.media.attrs], [self.identity("student")])
        self.assertEqual(self.media.attrs[0][0], self.room)

    def test_trainer_admins_and_outsiders_cannot_raise_a_hand(self):
        expected = {"trainer": 403, "other-trainer": 404, "outsider": 404, "guest": 404, "host": 404,
                    "viewer-admin": 404, "super-admin": 404, "plain-admin": 404}
        for who, code in expected.items():
            with self.subTest(who):
                self.assertEqual(self.hand(who).status_code, code)
        self.untouched()

    def test_anonymous_is_refused(self):
        r = self.client.post(f"/api/class-runs/{self.run_id}/hand", json={"raised": True})
        self.assertIn(r.status_code, (401, 403))

    def test_a_student_removed_from_the_batch_can_no_longer_raise_a_hand(self):
        with QaSession(self.engine) as db:
            db.execute(BatchEnrollment.__table__.delete().where(BatchEnrollment.student_id == self.ids["removed"]))
            db.commit()
        self.assertEqual(self.hand("removed").status_code, 404)
        self.untouched()

    def test_an_ended_class_and_a_missing_class_are_refused(self):
        with QaSession(self.engine) as db:
            db.get(ClassSessionRun, self.run_id).state = RUN_ENDED
            db.commit()
        self.assertEqual(self.hand("student").status_code, 409)
        r = self.client.post("/api/class-runs/987654/hand", headers=self.headers("student"), json={"raised": True})
        self.assertEqual(r.status_code, 404)
        self.untouched()

    def test_an_unreachable_media_server_gives_a_clear_503_not_a_crash(self):
        self.media.down = True
        r = self.hand("student")
        self.assertEqual(r.status_code, 503)
        self.assertIn("temporarily unavailable", r.json()["detail"])


class ModerationTests(ActionBase):
    def test_the_assigned_trainer_can_mute_kick_and_lower_a_students_hand(self):
        target = self.identity("student")
        for action in ("mute", "kick", "lower-hand"):
            self.assertEqual(self.act("trainer", action, target).status_code, 204, action)
        self.assertEqual(self.media.muted, [(self.room, target)])
        self.assertEqual(self.media.removed, [(self.room, target)])
        self.assertEqual(self.media.attrs, [(self.room, target, {"hand": ""})])

    def test_everyone_else_is_refused_and_nothing_reaches_the_media_server(self):
        target = self.identity("student")
        expected = {"student": 403, "removed": 403, "other-trainer": 404, "outsider": 404, "guest": 404,
                    "host": 404, "viewer-admin": 404, "super-admin": 404, "plain-admin": 404}
        for action in ("mute", "kick", "lower-hand"):
            for who, code in expected.items():
                with self.subTest(action=action, who=who):
                    self.assertEqual(self.act(who, action, target).status_code, code)
        self.untouched()

    def test_the_trainer_can_only_target_enrolled_students_of_this_class(self):
        bad = [self.identity("trainer"),           # themselves
               self.identity("outsider"),          # a student of another batch
               self.identity("super-admin"), self.identity("other-trainer"),
               "u999999", "evil", "u1; DROP", "../" + self.room]
        for identity in bad:
            for action in ("mute", "kick", "lower-hand"):
                with self.subTest(identity=identity, action=action):
                    self.assertIn(self.act("trainer", action, identity).status_code, (404, 422))
        self.untouched()

    def test_moderation_stays_inside_the_class_room_and_never_touches_recording(self):
        self.act("trainer", "kick", self.identity("student"))
        self.assertTrue(all(room == self.room for room, *_ in self.media.removed))
        self.assertEqual((self.media.started, self.media.stopped), ([], []))
        row = self.run_row()
        self.assertEqual(row.approval_status, "PENDING")

    def test_a_class_that_has_ended_cannot_be_moderated(self):
        with QaSession(self.engine) as db:
            db.get(ClassSessionRun, self.run_id).state = RUN_ENDED
            db.commit()
        self.assertEqual(self.act("trainer", "mute", self.identity("student")).status_code, 409)
        self.untouched()

    def test_media_server_outage_is_a_503(self):
        self.media.down = True
        self.assertEqual(self.act("trainer", "mute", self.identity("student")).status_code, 503)


class EndClassTests(ActionBase):
    def end(self, who):
        return self.client.post(f"/api/class-runs/{self.run_id}/end", headers=self.headers(who))

    def test_only_the_assigned_trainer_can_end_the_class(self):
        # Admins who may VIEW recording status get an explicit refusal (they can already see the class's
        # status); everyone without any standing gets 404. Nobody but the assigned trainer succeeds.
        expected = {"student": 403, "removed": 403, "other-trainer": 404, "outsider": 404, "guest": 404,
                    "host": 404, "viewer-admin": 403, "super-admin": 403, "plain-admin": 404}
        for who, code in expected.items():
            with self.subTest(who):
                self.assertEqual(self.end(who).status_code, code)
        self.assertEqual(self.run_row().state, "ROOM_OPEN", "nobody but the trainer changed the class")

    def test_anonymous_cannot_end_a_class(self):
        self.assertIn(self.client.post(f"/api/class-runs/{self.run_id}/end").status_code, (401, 403))
        self.assertEqual(self.run_row().state, "ROOM_OPEN")

    def test_trainer_ends_it_once_and_repeating_is_harmless(self):
        first = self.end("trainer")
        self.assertEqual(first.status_code, 200)
        self.assertEqual(self.run_row().state, RUN_ENDED)
        self.assertEqual(self.run_row().end_reason, "TRAINER_ENDED")
        self.assertEqual(self.end("trainer").status_code, 200)
        self.assertEqual(self.run_row().state, RUN_ENDED)

    def test_the_status_students_poll_reports_the_class_as_ended(self):
        self.end("trainer")
        self.assertEqual(self.status("student").json()["run_state"], RUN_ENDED)

    def test_an_unapproved_class_never_gets_a_recording_and_existing_recordings_are_kept(self):
        before = self.count(ClassRecording)
        self.end("trainer")
        self.assertEqual(self.run_row().recording_state, "NOT_RECORDED")
        self.assertEqual(self.count(ClassRecording), before)
        self.assertEqual((self.media.started, self.media.stopped), ([], []))

    def test_after_the_end_nobody_can_use_class_actions_or_approve_recording(self):
        self.end("trainer")
        self.assertEqual(self.hand("student").status_code, 409)
        self.assertEqual(self.act("trainer", "mute", self.identity("student")).status_code, 409)
        self.assertEqual(self.approve("trainer").status_code, 409)
        self.assertEqual(self.media.started, [])

    def test_the_end_route_name_keeps_the_class_run_guard_intact(self):
        from app.main import app
        for route in app.routes:
            path = getattr(route, "path", "")
            if path.startswith("/api/class-runs") and path.endswith("/end"):
                self.assertEqual(set(route.methods) - {"HEAD", "OPTIONS"}, {"POST"})


class AdminExclusionTests(ActionBase):
    def test_admins_cannot_join_a_class_through_any_join_endpoint(self):
        for who in ("viewer-admin", "super-admin", "plain-admin"):
            with self.subTest(who):
                self.assertIn(self.client.post(f"/api/class-runs/batch/{self.batch_id}/join", headers=self.headers(who),
                                               json={}).status_code, (403, 404))
                self.assertIn(self.client.post(f"/api/class-runs/{self.run_id}/join", headers=self.headers(who),
                                               json={}).status_code, (403, 404))
                self.assertEqual(self.client.post(f"/api/meetings/batch/{self.batch_id}/join", headers=self.headers(who),
                                                  json={}).status_code, 403)

    def test_legacy_batch_join_still_works_for_the_trainer_without_a_livekit_token(self):
        r = self.client.post(f"/api/meetings/batch/{self.batch_id}/join", headers=self.headers("trainer"), json={})
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(r.json().get("livekit_token"))


class GeneralMeetingLiveKitTests(ActionBase):
    def setUp(self):
        super().setUp()
        # General meetings use LiveKit only behind their own switch (LIVEKIT_MEETINGS_ENABLED, default off).
        patcher = patch.object(settings, "livekit_meetings_enabled", True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_meetings_stay_on_jitsi_when_livekit_is_configured_and_only_the_classroom_is_enabled(self):
        with patch.object(settings, "livekit_meetings_enabled", False), patch.object(settings, "livekit_classroom_enabled", True):
            body = self.join("host").json()
        self.assertEqual(body["status"], "READY")
        self.assertFalse(body.get("livekit_token"))
        self.assertFalse(body.get("livekit_url"))

    def meeting_token(self):
        with QaSession(self.engine) as db:
            return db.get(Meeting, self.meeting_id).meeting_token

    def join(self, who, **body):
        return self.client.post(f"/api/meetings/{self.meeting_token()}/join", headers=self.headers(who), json=body)

    def test_a_joined_member_gets_a_meeting_room_token_that_cannot_record(self):
        for who, moderator in (("host", True), ("guest", False)):
            with self.subTest(who):
                body = self.join(who).json()
                self.assertEqual(body["status"], "READY")
                self.assertEqual(body["livekit_url"], "wss://media.example.test")
                claims = jwt.decode(body["livekit_token"], options={"verify_signature": False})
                video = claims["video"]
                self.assertEqual(meeting_id_from_room_name(video["room"]), self.meeting_id)
                self.assertEqual(claims["sub"], self.identity(who))
                for grant in ("roomRecord", "roomCreate", "roomList", "roomAdmin", "hidden", "recorder", "canPublishData"):
                    self.assertFalse(video.get(grant), grant)
                self.assertEqual(jwt.decode(body["livekit_token"], options={"verify_signature": False})["metadata"].count("meeting"), 1)

    def test_no_token_without_standing_or_while_waiting_or_with_a_wrong_password(self):
        self.assertEqual(self.join("outsider").status_code, 403)
        with QaSession(self.engine) as db:
            meeting = db.get(Meeting, self.meeting_id)
            meeting.waiting_room_enabled = True
            row = db.scalar(select(MeetingParticipant).where(MeetingParticipant.meeting_id == meeting.id,
                                                             MeetingParticipant.user_id == self.ids["guest"]))
            row.status = ParticipantStatus.INVITED
            db.commit()
        waiting = self.join("guest").json()
        self.assertEqual(waiting["status"], "WAITING")
        self.assertIsNone(waiting.get("livekit_token"))
        with QaSession(self.engine) as db:
            from app.core.security import hash_password
            db.get(Meeting, self.meeting_id).password_hash = hash_password("secret-pass")
            db.commit()
        r = self.join("host", password="wrong")
        self.assertEqual(r.status_code, 401)
        self.assertNotIn("livekit_token", r.text)

    def test_without_a_media_server_the_join_has_no_livekit_fields(self):
        from unittest.mock import patch
        with patch.object(settings, "livekit_url", ""):
            body = self.join("host").json()
        self.assertIsNone(body.get("livekit_token"))
        self.assertIsNone(body.get("livekit_url"))

    def test_meeting_rooms_can_never_be_recorded_whatever_the_flags_say(self):
        room = meeting_room_name(self.meeting_id)
        self.assertNotEqual(room, room_name_for_run(self.meeting_id))
        with self.assertRaises(RecordingRefused):
            require_class_room(room)
        server = LiveKitMediaServer("http://127.0.0.1:1", "k", "s", output_prefix="/out", layout="speaker",
                                    preset="H264_720P_30", empty_timeout=60)
        with self.assertRaises(RecordingRefused):
            server.start_room_recording(room, "incoming/x.mp4")      # refused before any network call
        self.assertIsNone(meeting_id_from_room_name(room_name_for_run(self.run_id)))
        self.assertIsNone(meeting_id_from_room_name("meeting-1-0000000000000000"))
        # legacy flag ON (see fixture) must not matter; the recording endpoints still refuse
        r = self.client.post(f"/api/meetings/{self.meeting_id}/recordings/start", headers=self.headers("host"), json={})
        self.assertGreaterEqual(r.status_code, 400)
        self.assertEqual(self.media.started, [])

    def test_removing_a_participant_also_disconnects_them_from_the_room(self):
        with QaSession(self.engine) as db:
            pid = db.scalar(select(MeetingParticipant.id).where(MeetingParticipant.meeting_id == self.meeting_id,
                                                                MeetingParticipant.user_id == self.ids["guest"]))
        r = self.client.post(f"/api/meetings/{self.meeting_id}/participants/{pid}/remove", headers=self.headers("host"))
        self.assertEqual(r.status_code, 204)
        self.assertEqual(self.media.removed, [(meeting_room_name(self.meeting_id), self.identity("guest"))])
        self.assertEqual(self.join("guest").status_code, 403)       # and cannot rejoin

    def test_a_media_failure_does_not_block_the_removal(self):
        self.media.down = True
        with QaSession(self.engine) as db:
            pid = db.scalar(select(MeetingParticipant.id).where(MeetingParticipant.meeting_id == self.meeting_id,
                                                                MeetingParticipant.user_id == self.ids["guest"]))
        r = self.client.post(f"/api/meetings/{self.meeting_id}/participants/{pid}/remove", headers=self.headers("host"))
        self.assertEqual(r.status_code, 204)


class MeetingTokenShapeTests(unittest.TestCase):
    def test_token_has_no_data_publish_admin_or_record_rights(self):
        from unittest.mock import patch
        with patch.object(settings, "livekit_url", "wss://x.test"), patch.object(settings, "livekit_api_key", "k"), \
                patch.object(settings, "livekit_api_secret", "s" * 40):
            tok = meeting_join_token(identity="u1", name="N", room="meeting-1-" + "a" * 16, is_moderator=True)
        video = jwt.decode(tok, options={"verify_signature": False})["video"]
        self.assertTrue(video["roomJoin"])
        for grant in ("roomAdmin", "canPublishData", "roomRecord", "roomCreate", "roomList"):
            self.assertFalse(video.get(grant), grant)


if __name__ == "__main__":
    unittest.main()
