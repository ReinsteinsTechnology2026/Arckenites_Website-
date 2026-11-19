"""Verification, finalize, publication, OneDrive client, and secure playback.

The media files here are REAL MP4s generated and decoded with FFmpeg (PyAV), so
verification is genuinely exercised. The OneDrive tests run against a SIMULATED
Graph service (httpx.MockTransport): they prove our client follows the upload
protocol and fails closed, NOT that the real company drive accepts it.
"""
import base64
import hashlib
import json
import os
import tempfile
import unittest
from datetime import timedelta
from fractions import Fraction
from pathlib import Path
from unittest.mock import patch

import av
import httpx
import jwt
from sqlalchemy import select

from app.config import settings
from app.core import onedrive_store, recording_finalize, recording_publish, recording_verify
from app.core.class_runs import utcnow
from app.core.job_queue import PermanentFailure, RetryLater
from app.core.onedrive_store import OneDriveError, OneDriveStore, RemoteObject, quick_xor_hash_b64
from app.models import AuthAuditLog, BatchEnrollment, User, RoleEnum, AdminRole
from app.models.admin_role import SUPER_ADMIN_SLUG
from app.models.audit_log import AuthEventType
from app.models.batch_resources import ClassVideo
from app.models.class_run import BackgroundJob, ClassRecording, ClassSessionRun, RecordingEvent
from app import worker
from tests.test_job_queue_and_class_lifecycle import Harness


def make_mp4(path: Path, seconds: float = 3, fps: int = 25, with_audio: bool = True, audio_only: bool = False):
    container = av.open(str(path), "w")
    video = None if audio_only else container.add_stream("libx264", rate=fps)
    if video is not None:
        video.width, video.height, video.pix_fmt = 64, 64, "yuv420p"
    audio = None
    if with_audio or audio_only:
        audio = container.add_stream("aac", rate=48000)
        audio.layout = "mono"
    for i in range(int(seconds * fps) if video is not None else 0):
        frame = av.VideoFrame(64, 64, "yuv420p")
        for plane in frame.planes:
            plane.update(bytes([(i * 9) % 256]) * plane.buffer_size)
        frame.pts, frame.time_base = i, Fraction(1, fps)
        for packet in video.encode(frame):
            container.mux(packet)
    if video is not None:
        for packet in video.encode():
            container.mux(packet)
    if audio is not None:
        n = 0
        while n < seconds * 48000:
            frame = av.AudioFrame(format="fltp", layout="mono", samples=1024)
            for plane in frame.planes:
                plane.update(bytes(plane.buffer_size))
            frame.sample_rate, frame.pts, frame.time_base = 48000, n, Fraction(1, 48000)
            for packet in audio.encode(frame):
                container.mux(packet)
            n += 1024
        for packet in audio.encode():
            container.mux(packet)
    container.close()


class VerifyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.TemporaryDirectory(prefix="arck-media-", ignore_cleanup_errors=True)
        cls.root = Path(cls.dir.name)
        make_mp4(cls.root / "good.mp4", 3)
        make_mp4(cls.root / "silent.mp4", 3, with_audio=False)
        make_mp4(cls.root / "audio.m4a", 3, audio_only=True)
        make_mp4(cls.root / "tiny.mp4", 0.5, fps=10)
        make_mp4(cls.root / "long.mp4", 130, fps=1, with_audio=False)

    @classmethod
    def tearDownClass(cls):
        cls.dir.cleanup()

    def verify(self, name, **kw):
        return recording_verify.verify_media_file(self.root / name, **kw)

    def test_a_real_recording_with_audio_and_video_passes(self):
        r = self.verify("good.mp4")
        self.assertTrue(r.ok, r.detail)
        self.assertTrue(r.has_video and r.has_audio)
        self.assertAlmostEqual(r.duration_seconds, 3, delta=0.2)
        self.assertEqual((r.video_codec, r.width, r.height), ("h264", 64, 64))
        self.assertGreater(r.packets, 50)

    def test_video_without_audio_still_passes_and_is_reported_as_silent(self):
        r = self.verify("silent.mp4")
        self.assertTrue(r.ok)
        self.assertFalse(r.has_audio)

    def test_truncated_garbage_empty_and_missing_files_fail(self):
        data = (self.root / "good.mp4").read_bytes()
        (self.root / "trunc.mp4").write_bytes(data[: len(data) // 2])
        (self.root / "junk.mp4").write_bytes(os.urandom(4096))
        (self.root / "empty.mp4").write_bytes(b"")
        for name, code in (("trunc.mp4", "unreadable"), ("junk.mp4", "unreadable"), ("empty.mp4", "empty"),
                           ("nothing.mp4", "missing")):
            with self.subTest(name):
                r = self.verify(name)
                self.assertFalse(r.ok)
                self.assertEqual(r.error_code, code)

    def test_a_file_cut_in_the_middle_of_its_media_data_fails(self):
        data = bytearray((self.root / "good.mp4").read_bytes())
        data[len(data) // 2:] = b"\x00" * (len(data) - len(data) // 2)  # zeroed tail, header intact
        (self.root / "zeroed.mp4").write_bytes(bytes(data))
        self.assertFalse(self.verify("zeroed.mp4").ok)

    def test_audio_only_and_too_short_files_are_rejected(self):
        self.assertEqual(self.verify("audio.m4a").error_code, "no_video_stream")
        self.assertEqual(self.verify("tiny.mp4").error_code, "too_short")

    def test_duration_must_be_plausible_for_the_recorded_window(self):
        self.assertTrue(self.verify("good.mp4", expected_window_seconds=3).ok)
        self.assertEqual(self.verify("good.mp4", expected_window_seconds=900).error_code, "duration_implausible")  # far too short
        self.assertEqual(self.verify("long.mp4", expected_window_seconds=5).error_code, "duration_implausible")   # far too long
        self.assertTrue(self.verify("long.mp4", expected_window_seconds=130).ok)


class FinalizeAndPublishTests(Harness):
    def setUp(self):
        super().setUp()
        self.run_id = self.make_run(state="ENDED", approval="APPROVED", rec="PROCESSING")
        (self.storage / "incoming").mkdir(exist_ok=True)

    def make_recording(self, *, content="good", state="FINALIZING", started=True, run_id=None):
        rid_key = f"incoming/{os.urandom(8).hex()}.mp4"
        path = self.storage / rid_key
        if content == "good":
            make_mp4(path, 3)
        elif content == "junk":
            path.write_bytes(os.urandom(4096))
        now = utcnow()
        with self.sf() as db:
            rec = ClassRecording(run_id=run_id or self.run_id, batch_id=self.batch_id, capture_state=state, egress_id="EG_F",
                                 staging_key=rid_key, started_at=now - timedelta(seconds=3) if started else None,
                                 ended_at=now if started else None)
            db.add(rec)
            db.commit()
            return str(rec.id), path

    def finalize(self, rid):
        with self.sf() as db:
            return recording_finalize.finalize_recording(db, rid)

    def rec(self, rid):
        with self.sf() as db:
            row = db.get(ClassRecording, __import__("uuid").UUID(rid))
            db.expunge(row)
            return row

    # -- finalize -----------------------------------------------------------------
    def test_a_verified_file_becomes_stored_with_its_real_metadata(self):
        rid, path = self.make_recording()
        self.assertEqual(self.finalize(rid), "stored")
        rec = self.rec(rid)
        self.assertEqual(rec.capture_state, "STORED")
        self.assertEqual(rec.sha256, hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertEqual(rec.size_bytes, path.stat().st_size)
        self.assertAlmostEqual(rec.duration_seconds, 3, delta=1)
        self.assertIsNotNone(rec.verified_at)
        self.assertEqual(json.loads(rec.media_info)["verified"]["codec"], "h264")
        self.assertEqual(self.count(AuthAuditLog, AuthAuditLog.event_type == AuthEventType.recording_processed), 1)

    def test_stored_is_not_published_while_the_final_copy_is_not_verified(self):
        rid, _ = self.make_recording()
        self.finalize(rid)
        rec = self.rec(rid)
        self.assertEqual(rec.publication_state, "NOT_PUBLISHED")
        self.assertEqual(self.count(ClassVideo, ClassVideo.recording_id.is_not(None)), 0)
        self.assertEqual(self.jobs("recording.upload") + self.jobs("recording.publish"), [])
        self.assertEqual(self.run_row(self.run_id).recording_state, "PROCESSING")  # not "published"
        with self.sf() as db:
            events = [e.event for e in db.scalars(select(RecordingEvent))]
        self.assertIn("awaiting_remote_storage", events)
        self.assertEqual(recording_publish.publish_recording(self.sf(), rid), "no_verified_final_copy")

    def test_a_corrupt_file_is_failed_not_stored_and_is_kept_for_inspection(self):
        rid, path = self.make_recording(content="junk")
        self.assertEqual(self.finalize(rid), "failed")
        rec = self.rec(rid)
        self.assertEqual((rec.capture_state, rec.error_code), ("FAILED", "verification_unreadable"))
        self.assertTrue(path.exists())  # never deleted automatically
        self.assertEqual(self.run_row(self.run_id).recording_state, "FAILED")
        self.assertEqual(self.count(AuthAuditLog, AuthAuditLog.event_type == AuthEventType.recording_failed), 1)
        self.assertEqual(self.jobs("recording.upload") + self.jobs("recording.publish"), [])

    def test_a_missing_file_is_retried_then_reported_failed(self):
        rid, path = self.make_recording()
        path.unlink()
        with self.assertRaises(RetryLater):
            self.finalize(rid)
        with self.sf() as db:
            job_queue_job = recording_finalize.on_finalize_dead
            fake = type("J", (), {"payload": json.dumps({"recording_id": rid}), "last_error": "gave up"})()
            job_queue_job(db, fake)
            db.commit()
        rec = self.rec(rid)
        self.assertEqual((rec.capture_state, rec.error_code), ("FAILED", "finalize_failed"))

    def test_finalize_is_idempotent_and_ignores_recordings_not_ready(self):
        rid, _ = self.make_recording()
        self.assertEqual(self.finalize(rid), "stored")
        self.assertEqual(self.finalize(rid), "already_stored")
        early, _ = self.make_recording(state="RECORDING", run_id=self.make_run(state="LIVE"))  # one active recording per class
        self.assertEqual(self.finalize(early), "not_ready")

    def test_an_implausible_duration_is_rejected(self):
        rid, _ = self.make_recording()
        with self.sf() as db:  # the class was "recorded" for an hour, but the file is 3 seconds
            rec = db.get(ClassRecording, __import__("uuid").UUID(rid))
            rec.started_at, rec.ended_at = utcnow() - timedelta(hours=1), utcnow()
            db.commit()
        self.assertEqual(self.finalize(rid), "failed")
        self.assertEqual(self.rec(rid).error_code, "verification_duration_implausible")

    # -- publication ----------------------------------------------------------------
    def stored(self):
        rid, path = self.make_recording()
        self.finalize(rid)
        return rid, path

    def publish(self, rid):
        with self.sf() as db:
            return recording_publish.publish_recording(db, rid)

    def test_development_mode_publishes_from_staging_once_and_labels_it_honestly(self):
        with patch.object(settings, "recording_publish_from_staging", True), patch.object(settings, "env", "test"):
            rid, _ = self.stored()
            self.assertEqual(len(self.jobs("recording.publish")), 1)
            self.assertEqual(self.publish(rid), "published")
            self.assertEqual(self.publish(rid), "already_published")  # a retried job
            self.assertEqual(self.publish(rid), "already_published")
        rec = self.rec(rid)
        self.assertEqual((rec.publication_state, rec.storage_provider), ("PUBLISHED", "LOCAL_STAGING"))  # not OneDrive
        self.assertIsNone(rec.remote_item_id)
        self.assertEqual(self.count(ClassVideo, ClassVideo.recording_id == rec.id), 1)
        self.assertEqual(self.run_row(self.run_id).recording_state, "PUBLISHED")
        self.assertEqual(self.count(AuthAuditLog, AuthAuditLog.event_type == AuthEventType.recording_published), 1)
        with self.sf() as db:
            video = db.scalars(select(ClassVideo).where(ClassVideo.recording_id == rec.id)).one()
        self.assertEqual((video.source, video.video_url, video.batch_id), ("RECORDING", None, self.batch_id))

    def test_the_development_shortcut_is_impossible_in_production(self):
        with patch.object(settings, "recording_publish_from_staging", True), patch.object(settings, "env", "production"):
            self.assertFalse(recording_publish.dev_publish_from_staging_allowed())
            rid, _ = self.stored()
            self.assertEqual(self.jobs("recording.publish"), [])
            self.assertEqual(self.publish(rid), "no_verified_final_copy")
        self.assertEqual(self.count(ClassVideo, ClassVideo.recording_id.is_not(None)), 0)

    def test_onedrive_flow_uploads_verifies_then_publishes_and_never_rerecords(self):
        class Ready(OneDriveStore):
            uploads = []

            def readiness(self):
                return True, None

            def upload_file(self, local_path, remote_name):
                Ready.uploads.append(remote_name)
                return RemoteObject(item_id="ITEM1", size=local_path.stat().st_size, quick_xor_hash="h")

        with patch.object(recording_publish, "OneDriveStore", Ready):
            rid, _ = self.stored()
            self.assertEqual(len(self.jobs("recording.upload")), 1)
            with self.sf() as db:
                self.assertEqual(recording_publish.upload_recording(db, rid), "uploaded")
                self.assertEqual(recording_publish.upload_recording(db, rid), "already_uploaded")
            rec = self.rec(rid)
            self.assertEqual((rec.storage_provider, rec.remote_item_id), ("ONEDRIVE", "ITEM1"))
            self.assertIsNotNone(rec.remote_verified_at)
            self.assertEqual(rec.publication_state, "PUBLISHING")  # uploaded, but not visible yet
            self.assertEqual(self.count(ClassVideo, ClassVideo.recording_id.is_not(None)), 0)
            self.assertEqual(self.publish(rid), "published")
            self.assertEqual(Ready.uploads, [f"{rid}.mp4"])        # uploaded exactly once
        self.assertEqual(self.media.started, [])                    # and the class was never re-recorded

    def test_a_failed_upload_is_retried_and_only_an_exhausted_one_is_reported_failed(self):
        class Flaky(OneDriveStore):
            def readiness(self):
                return True, None

            def upload_file(self, local_path, remote_name):
                raise OneDriveError("The uploaded file's content hash does not match.")

        with patch.object(recording_publish, "OneDriveStore", Flaky):
            rid, _ = self.stored()
            with self.sf() as db, self.assertRaises(RetryLater):
                recording_publish.upload_recording(db, rid)
            self.assertIsNone(self.rec(rid).remote_verified_at)
            self.assertEqual(self.count(ClassVideo, ClassVideo.recording_id.is_not(None)), 0)
            with self.sf() as db:
                fake = type("J", (), {"payload": json.dumps({"recording_id": rid}), "last_error": "hash mismatch"})()
                recording_publish.on_dead(db, fake)
                db.commit()
        rec = self.rec(rid)
        self.assertEqual((rec.publication_state, rec.error_code), ("PUBLISH_FAILED", "publication_failed"))
        self.assertEqual(rec.capture_state, "STORED")  # the verified file is untouched; only publication failed

    def test_reconcile_queues_waiting_recordings_once_onedrive_becomes_ready(self):
        rid, _ = self.stored()
        with self.sf() as db:
            self.assertEqual(recording_publish.requeue_unpublished(db), 0)  # not ready: nothing queued
        class Ready(OneDriveStore):
            def readiness(self):
                return True, None
        with patch.object(recording_publish, "OneDriveStore", Ready):
            with self.sf() as db:
                self.assertEqual(recording_publish.requeue_unpublished(db), 1)
                self.assertEqual(recording_publish.requeue_unpublished(db), 1)
                db.commit()
        self.assertEqual(len(self.jobs("recording.upload")), 1)  # deduplicated

    def test_publication_requires_a_verified_stored_recording(self):
        rid, _ = self.make_recording()  # FINALIZING, not yet verified
        with patch.object(settings, "recording_publish_from_staging", True), patch.object(settings, "env", "test"):
            self.assertEqual(self.publish(rid), "not_publishable")
        self.assertEqual(self.count(ClassVideo, ClassVideo.recording_id.is_not(None)), 0)


# ---------------------------------------------------------------------------
# QuickXorHash and the OneDrive client
# ---------------------------------------------------------------------------

def reference_quick_xor(data: bytes) -> str:
    """An independent, slow transcription of Microsoft's published QuickXorHash
    reference algorithm (64/64/32-bit cells), used to cross-check the fast one."""
    width, shift = 160, 11
    cells, m64 = [0, 0, 0], (1 << 64) - 1
    cb, idx, off = len(data), 0, 0
    for i in range(min(cb, width)):
        last = idx == len(cells) - 1
        bits = 32 if last else 64
        if off <= bits - 8:
            for j in range(i, cb, width):
                cells[idx] ^= (data[j] << off) & m64
        else:
            idx2 = 0 if last else idx + 1
            xored = 0
            for j in range(i, cb, width):
                xored ^= data[j]
            cells[idx] ^= (xored << off) & m64
            cells[idx2] ^= xored >> (bits - off)
        off += shift
        if off >= bits:
            idx = 0 if last else idx + 1
            off -= bits
    out = bytearray(cells[0].to_bytes(8, "little") + cells[1].to_bytes(8, "little") + (cells[2] & 0xFFFFFFFF).to_bytes(4, "little"))
    for k, b in enumerate(cb.to_bytes(8, "little")):
        out[12 + k] ^= b
    return base64.b64encode(bytes(out)).decode()


class QuickXorTests(unittest.TestCase):
    def test_fast_implementation_matches_the_reference_on_many_sizes(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            for size in (0, 1, 7, 19, 20, 21, 159, 160, 161, 320, 999, 5000, 160 * 7 + 3):
                data = os.urandom(size)
                path = Path(tmp) / f"f{size}"
                path.write_bytes(data)
                self.assertEqual(quick_xor_hash_b64(path), reference_quick_xor(data), f"size {size}")

    def test_it_is_sensitive_to_content_and_length_and_independent_of_read_size(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            data = os.urandom(160 * 200 + 17)
            a, b, c = (Path(tmp) / n for n in "abc")
            a.write_bytes(data)
            b.write_bytes(data[:-1] + bytes([data[-1] ^ 1]))
            c.write_bytes(data + b"\x00")
            self.assertNotEqual(quick_xor_hash_b64(a), quick_xor_hash_b64(b))
            self.assertNotEqual(quick_xor_hash_b64(a), quick_xor_hash_b64(c))
            self.assertEqual(quick_xor_hash_b64(a, read_blocks=1), quick_xor_hash_b64(a, read_blocks=4096))


class FakeGraph:
    """A simulated Graph drive with resumable upload sessions."""

    def __init__(self, *, fail_chunk_once=False, expire_session=False, tamper=False, hash_after=0, wrong_size=False):
        self.requests, self.received, self.fail_chunk_once, self.expire = [], bytearray(), fail_chunk_once, expire_session
        self.tamper, self.hash_after, self.verify_calls, self.wrong_size, self.failed = tamper, hash_after, 0, wrong_size, False
        self.total = None
        self.client = httpx.Client(transport=httpx.MockTransport(self.handle))

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = str(request.url)
        if request.method == "POST" and url.endswith("/createUploadSession"):
            return httpx.Response(200, json={"uploadUrl": "https://upload.example.test/session/abc"})
        if url.startswith("https://upload.example.test"):
            if request.method == "GET":
                return httpx.Response(200, json={"nextExpectedRanges": [f"{len(self.received)}-"]})
            if self.expire:
                return httpx.Response(404)
            if self.fail_chunk_once and not self.failed and len(self.received) > 0:
                self.failed = True
                return httpx.Response(503)
            start, rest = request.headers["Content-Range"].removeprefix("bytes ").split("-")
            end, total = rest.split("/")
            if int(start) != len(self.received):   # the server only accepts the next expected range
                return httpx.Response(416)
            self.received += request.content
            self.total = int(total)
            if len(self.received) >= self.total:
                return httpx.Response(201, json={"id": "ITEM-9"})
            return httpx.Response(202, json={"nextExpectedRanges": [f"{len(self.received)}-"]})
        if request.method == "GET" and "/items/" in url:
            self.verify_calls += 1
            body = bytes(self.received)
            h = None
            if self.verify_calls > self.hash_after:
                with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
                    p = Path(tmp) / "x"
                    p.write_bytes(body + (b"x" if self.tamper else b""))
                    h = quick_xor_hash_b64(p)
            size = len(body) + (5 if self.wrong_size else 0)
            return httpx.Response(200, json={"id": "ITEM-9", "size": size, "file": {"hashes": {"quickXorHash": h} if h else {}}})
        return httpx.Response(500)


class OneDriveClientTests(unittest.TestCase):
    CHUNK = 320 * 1024

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        self.file = Path(self.tmp.name) / "rec.mp4"
        self.file.write_bytes(os.urandom(self.CHUNK * 2 + 4321))
        for p in (patch.object(settings, "onedrive_upload_enabled", True),):
            p.start()
            self.addCleanup(p.stop)

    def store(self, graph, **kw):
        return OneDriveStore(drive_id="DRIVE1", folder="Arck/Recordings", chunk_bytes=self.CHUNK, token_provider=lambda: "TOKEN-VALUE",
                             http=graph.client, sleep=lambda s: None, **kw)

    def test_a_chunked_upload_is_verified_by_size_and_hash(self):
        graph = FakeGraph()
        remote = self.store(graph).upload_file(self.file, "abc.mp4")
        self.assertEqual(bytes(graph.received), self.file.read_bytes())
        self.assertEqual((remote.item_id, remote.size), ("ITEM-9", self.file.stat().st_size))
        puts = [r for r in graph.requests if r.method == "PUT"]
        self.assertEqual(len(puts), 3)  # 2 full chunks + remainder
        self.assertTrue(all(int(r.headers["Content-Length"]) <= self.CHUNK for r in puts))

    def test_the_session_request_asks_for_a_private_file_in_the_configured_folder(self):
        graph = FakeGraph()
        self.store(graph).upload_file(self.file, "abc.mp4")
        create = next(r for r in graph.requests if str(r.url).endswith("/createUploadSession"))
        self.assertIn("/drives/DRIVE1/root:/Arck/Recordings/abc.mp4:/createUploadSession", str(create.url))
        self.assertEqual(create.headers["Authorization"], "Bearer TOKEN-VALUE")
        self.assertFalse([r for r in graph.requests if "createLink" in str(r.url) or "/permissions" in str(r.url)])  # never a share link

    def test_the_pre_authenticated_upload_url_never_receives_our_token(self):
        graph = FakeGraph()
        self.store(graph).upload_file(self.file, "abc.mp4")
        for r in graph.requests:
            if str(r.url).startswith("https://upload.example.test"):
                self.assertNotIn("authorization", {k.lower() for k in r.headers})

    def test_a_failed_chunk_resumes_from_what_the_server_has(self):
        graph = FakeGraph(fail_chunk_once=True)
        self.store(graph).upload_file(self.file, "abc.mp4")
        self.assertEqual(bytes(graph.received), self.file.read_bytes())  # complete, no gaps, no duplicates

    def test_an_expired_session_fails(self):
        with self.assertRaisesRegex(OneDriveError, "expired"):
            self.store(FakeGraph(expire_session=True)).upload_file(self.file, "abc.mp4")

    def test_a_size_mismatch_or_hash_mismatch_is_never_accepted(self):
        with self.assertRaisesRegex(OneDriveError, "size"):
            self.store(FakeGraph(wrong_size=True)).upload_file(self.file, "abc.mp4")
        with self.assertRaisesRegex(OneDriveError, "hash does not match"):
            self.store(FakeGraph(tamper=True)).upload_file(self.file, "abc.mp4")

    def test_it_waits_for_onedrive_to_compute_the_hash_but_not_forever(self):
        self.assertEqual(self.store(FakeGraph(hash_after=2)).upload_file(self.file, "abc.mp4").item_id, "ITEM-9")
        with self.assertRaisesRegex(OneDriveError, "not reported a content hash"):
            self.store(FakeGraph(hash_after=10 ** 6)).upload_file(self.file, "abc.mp4")

    def test_it_refuses_to_run_unless_enabled_configured_and_non_empty(self):
        graph = FakeGraph()
        with patch.object(settings, "onedrive_upload_enabled", False):
            with self.assertRaises(onedrive_store.OneDriveNotReady):
                self.store(graph).upload_file(self.file, "a.mp4")
        with self.assertRaises(onedrive_store.OneDriveNotReady):
            OneDriveStore(drive_id="", folder="x", token_provider=lambda: "t", http=graph.client).upload_file(self.file, "a.mp4")
        empty = Path(self.tmp.name) / "empty.mp4"
        empty.write_bytes(b"")
        with self.assertRaisesRegex(OneDriveError, "empty"):
            self.store(graph).upload_file(empty, "a.mp4")
        self.assertEqual(graph.requests, [])  # none of those refusals touched the network

    def test_readiness_is_fail_closed_by_default(self):
        with patch.object(settings, "onedrive_upload_enabled", False):
            self.assertFalse(OneDriveStore().readiness()[0])
        with patch.object(settings, "onedrive_upload_enabled", True), patch.object(settings, "onedrive_drive_id", ""):
            self.assertIn("destination", OneDriveStore().readiness()[1])
        with patch.object(settings, "onedrive_upload_enabled", True), patch.object(settings, "onedrive_drive_id", "D"):
            self.assertFalse(OneDriveStore().readiness()[0])  # no Graph credentials in this environment

    def test_chunk_size_must_be_a_multiple_of_320_kib(self):
        with self.assertRaises(ValueError):
            OneDriveStore(drive_id="D", folder="f", chunk_bytes=1000)

    def test_a_graph_failure_message_never_contains_the_token(self):
        class Boom(httpx.Client):
            pass
        graph = FakeGraph()
        graph.client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(401, json={"error": {"message": "TOKEN-VALUE"}})))
        with self.assertRaises(OneDriveError) as ctx:
            self.store(graph).upload_file(self.file, "a.mp4")
        self.assertNotIn("TOKEN-VALUE", str(ctx.exception))


# ---------------------------------------------------------------------------
# Secure playback + Class Videos
# ---------------------------------------------------------------------------

class PlaybackTests(Harness):
    def setUp(self):
        super().setUp()
        self.run_id = self.make_run(state="ENDED", approval="APPROVED", rec="PUBLISHED")
        (self.storage / "incoming").mkdir(exist_ok=True)
        self.key = f"incoming/{os.urandom(8).hex()}.mp4"
        self.path = self.storage / self.key
        make_mp4(self.path, 3)
        self.data = self.path.read_bytes()
        with self.sf() as db:
            rec = ClassRecording(run_id=self.run_id, batch_id=self.batch_id, capture_state="STORED",
                                 publication_state="PUBLISHED", staging_key=self.key, size_bytes=len(self.data),
                                 duration_seconds=3, verified_at=utcnow(), published_at=utcnow(), storage_provider="LOCAL_STAGING")
            db.add(rec)
            db.flush()
            db.add(ClassVideo(batch_id=self.batch_id, title="Lesson 1", source="RECORDING", recording_id=rec.id))
            db.add(ClassVideo(batch_id=self.batch_id, title="Posted link", video_url="https://example.com/v"))
            sa = AdminRole(name="Super Admin", slug=SUPER_ADMIN_SLUG, is_system=True)
            db.add(sa)
            db.flush()
            db.add(User(username="boss", full_name="Boss", role=RoleEnum.admin, admin_role_id=sa.id, password_hash="x",
                        is_active=True, must_change_password=False))
            db.commit()
            self.rid = str(rec.id)
        with self.sf() as db:
            self.ids["boss"] = db.scalar(select(User.id).where(User.username == "boss"))

    def token(self, who="student", rid=None):
        r = self.client.post(f"/api/recordings/{rid or self.rid}/playback-token", headers=self.headers(who))
        return r

    def stream(self, token, rid=None, **kw):
        return self.client.get(f"/api/recordings/{rid or self.rid}/stream", params={"t": token}, **kw)

    def test_stream_rate_limit_spares_normal_seeking_and_refuses_abuse_without_a_database_write(self):
        from app.core.rate_limit import limiter
        from app.models.audit_log import AuthEventType
        t = self.token("student").json()["stream_url"].split("?t=")[1]
        enabled = limiter.enabled
        limiter.enabled = True
        limiter.reset()
        try:
            with self.sf() as db:
                before = db.query(AuthAuditLog).filter(AuthAuditLog.event_type == AuthEventType.rate_limited).count()
            seeks = [self.stream(t, headers={"Range": f"bytes={i}-{i}"}).status_code for i in range(150)]
            self.assertEqual(set(seeks), {206}, "normal playback (many Range requests) must never be throttled")
            codes = [self.stream(t).status_code for _ in range(160)]  # 310 in the minute, over the 300 cap
            self.assertIn(429, codes)
            self.assertEqual(codes[0], 200)
            refused = self.stream(t)
            self.assertEqual(refused.status_code, 429)
            self.assertNotIn(t, refused.text)
            with self.sf() as db:  # refused requests are cheap: no audit row per request
                after = db.query(AuthAuditLog).filter(AuthAuditLog.event_type == AuthEventType.rate_limited).count()
            self.assertEqual(after, before)
            # throttling the stream does not stop an authorized viewer from asking for a fresh link
            self.assertEqual(self.token("student").status_code, 200)
        finally:
            limiter.reset()
            limiter.enabled = enabled

    def good_token(self, who="student"):
        return self.token(who).json()["stream_url"].split("t=")[1]

    # -- who may watch ---------------------------------------------------------------
    def test_enrolled_students_and_the_batch_trainer_can_watch(self):
        for who in ("student", "trainer"):
            with self.subTest(who):
                self.assertEqual(self.token(who).status_code, 200)

    def test_a_super_admin_cannot_play_a_recording(self):
        # Approved rule: admins may see status/metadata but never play or download.
        self.assertEqual(self.token("boss").status_code, 404)

    def test_other_batches_other_trainers_and_the_unauthenticated_cannot(self):
        for who in ("outsider", "other-trainer"):
            with self.subTest(who):
                self.assertEqual(self.token(who).status_code, 404)  # same as "doesn't exist"
        self.assertEqual(self.client.post(f"/api/recordings/{self.rid}/playback-token").status_code, 401)
        self.assertEqual(self.token("student", rid="not-a-uuid").status_code, 404)
        self.assertEqual(self.token("student", rid="00000000-0000-0000-0000-000000000000").status_code, 404)

    def test_unpublished_revoked_or_deleted_recordings_cannot_be_played(self):
        for change in ({"publication_state": "REVOKED"}, {"publication_state": "NOT_PUBLISHED"}, {"deleted_at": utcnow()}):
            with self.subTest(change):
                with self.sf() as db:
                    rec = db.get(ClassRecording, __import__("uuid").UUID(self.rid))
                    rec.publication_state, rec.deleted_at = "PUBLISHED", None
                    for k, v in change.items():
                        setattr(rec, k, v)
                    db.commit()
                self.assertEqual(self.token("student").status_code, 404)
                self.assertEqual(self.stream("x" * 20).status_code, 404)

    # -- the stream -----------------------------------------------------------------------
    def test_full_and_ranged_responses_are_exact(self):
        t = self.good_token()
        full = self.stream(t)
        self.assertEqual((full.status_code, full.content), (200, self.data))
        self.assertEqual(full.headers["accept-ranges"], "bytes")
        part = self.stream(t, headers={"Range": "bytes=10-99"})
        self.assertEqual((part.status_code, part.content), (206, self.data[10:100]))
        self.assertEqual(part.headers["content-range"], f"bytes 10-99/{len(self.data)}")
        tail = self.stream(t, headers={"Range": "bytes=-50"})
        self.assertEqual((tail.status_code, tail.content), (206, self.data[-50:]))
        rest = self.stream(t, headers={"Range": f"bytes={len(self.data) - 10}-"})
        self.assertEqual(rest.content, self.data[-10:])
        over = self.stream(t, headers={"Range": f"bytes=5-{len(self.data) * 9}"})
        self.assertEqual(over.content, self.data[5:])

    def test_unsatisfiable_ranges_get_416_and_odd_ranges_get_the_whole_file(self):
        t = self.good_token()
        bad = self.stream(t, headers={"Range": f"bytes={len(self.data) + 5}-"})
        self.assertEqual(bad.status_code, 416)
        self.assertEqual(bad.headers["content-range"], f"bytes */{len(self.data)}")
        self.assertEqual(self.stream(t, headers={"Range": "bytes=-0"}).status_code, 416)
        self.assertEqual(self.stream(t, headers={"Range": "bytes=0-1,5-9"}).status_code, 200)  # multi-range: whole file
        self.assertEqual(self.stream(t, headers={"Range": "garbage"}).status_code, 200)

    def test_head_returns_headers_without_a_body(self):
        t = self.good_token()
        head = self.client.head(f"/api/recordings/{self.rid}/stream", params={"t": t})
        self.assertEqual((head.status_code, head.content), (200, b""))
        self.assertEqual(int(head.headers["content-length"]), len(self.data))

    def test_responses_are_private_and_never_leak_paths(self):
        r = self.stream(self.good_token())
        self.assertIn("no-store", r.headers["cache-control"])
        self.assertEqual(r.headers["content-type"], "video/mp4")
        self.assertEqual(r.headers["x-content-type-options"], "nosniff")
        self.assertNotIn("attachment", r.headers.get("content-disposition", ""))
        self.assertNotIn(self.key, json.dumps(dict(r.headers)))

    def test_tokens_are_scoped_expiring_and_not_interchangeable(self):
        now = utcnow()
        def mint(**over):
            claims = {"purpose": "recording-playback", "rid": self.rid, "sub": str(self.ids["student"]),
                      "iat": now, "exp": now + timedelta(minutes=5)}
            claims.update(over)
            return jwt.encode(claims, settings.jwt_secret, algorithm="HS256")
        self.assertEqual(self.stream(mint()).status_code, 200)
        self.assertEqual(self.stream(mint(exp=now - timedelta(minutes=1))).status_code, 401)
        self.assertEqual(self.stream(mint(rid="00000000-0000-0000-0000-000000000000")).status_code, 401)
        self.assertEqual(self.stream(mint(purpose="something-else")).status_code, 401)
        self.assertEqual(self.stream(jwt.encode({"purpose": "recording-playback", "rid": self.rid, "sub": "1", "exp": now + timedelta(minutes=5)}, "wrong-secret-" * 4, algorithm="HS256")).status_code, 401)
        self.assertEqual(self.stream("not.a.token.at.all").status_code, 401)
        access = self.headers("student")["Authorization"].split()[1]
        self.assertEqual(self.stream(access).status_code, 401)  # a normal login token is not a playback token
        self.assertEqual(self.client.get(f"/api/recordings/{self.rid}/stream").status_code, 422)  # no token at all

    def test_a_token_minted_for_one_user_still_obeys_that_users_current_access(self):
        t = self.good_token("student")
        self.assertEqual(self.stream(t).status_code, 200)
        with self.sf() as db:  # the student is removed from the batch while the token is still valid
            for row in db.scalars(select(BatchEnrollment).where(BatchEnrollment.student_id == self.ids["student"])):
                db.delete(row)
            db.commit()
        self.assertEqual(self.stream(t).status_code, 404)
        self.assertEqual(self.token("student").status_code, 404)

    def test_a_deactivated_account_loses_playback_immediately(self):
        t = self.good_token("student")
        with self.sf() as db:
            db.get(User, self.ids["student"]).is_active = False
            db.commit()
        self.assertEqual(self.stream(t).status_code, 404)

    def test_a_token_for_another_user_cannot_be_used_by_a_different_batchs_student(self):
        mine = self.good_token("student")
        # an outsider replaying a stolen token still maps to the token's own user, never to themselves
        self.assertEqual(self.stream(mine).status_code, 200)
        outsider_token = jwt.encode({"purpose": "recording-playback", "rid": self.rid, "sub": str(self.ids["outsider"]),
                                     "exp": utcnow() + timedelta(minutes=5)}, settings.jwt_secret, algorithm="HS256")
        self.assertEqual(self.stream(outsider_token).status_code, 404)

    def test_a_missing_or_altered_file_is_reported_unavailable_not_served(self):
        t = self.good_token()
        self.path.write_bytes(self.data + b"tampered")
        self.assertEqual(self.stream(t).status_code, 503)
        self.path.unlink()
        self.assertEqual(self.stream(t).status_code, 503)

    def test_playback_authorization_is_audited(self):
        self.token("student")
        self.assertEqual(self.count(AuthAuditLog, AuthAuditLog.event_type == AuthEventType.recording_playback), 1)

    def test_a_real_recording_streamed_through_the_api_is_still_a_playable_file(self):
        r = self.stream(self.good_token())
        out = self.storage / "roundtrip.mp4"
        out.write_bytes(r.content)
        self.assertTrue(recording_verify.verify_media_file(out).ok)

    # -- Class Videos listings ----------------------------------------------------------------
    def test_students_see_their_batchs_recording_beside_the_posted_links(self):
        videos = self.client.get("/api/students/me/videos", headers=self.headers("student")).json()
        by_title = {v["title"]: v for v in videos}
        self.assertEqual(set(by_title), {"Lesson 1", "Posted link"})
        rec = by_title["Lesson 1"]
        self.assertEqual((rec["source"], rec["video_url"], rec["recording_id"], rec["duration_seconds"]), ("RECORDING", None, self.rid, 3))
        self.assertEqual(rec["trainer_name"], "Trainer")
        self.assertEqual(by_title["Posted link"]["video_url"], "https://example.com/v")  # manual links unchanged
        self.assertNotIn(self.key, json.dumps(videos))

    def test_other_batches_never_see_it_and_revoked_recordings_disappear(self):
        other = self.client.get("/api/students/me/videos", headers=self.headers("outsider")).json()
        self.assertEqual([v["title"] for v in other], [])
        with self.sf() as db:
            db.get(ClassRecording, __import__("uuid").UUID(self.rid)).publication_state = "REVOKED"
            db.commit()
        mine = self.client.get("/api/students/me/videos", headers=self.headers("student")).json()
        self.assertEqual([v["title"] for v in mine], ["Posted link"])

    def test_trainer_and_admin_lists_include_recordings_and_cannot_delete_them(self):
        trainer = self.client.get(f"/api/staff/me/batches/{self.batch_id}/videos", headers=self.headers("trainer")).json()
        self.assertEqual({v["source"] for v in trainer}, {"RECORDING", "LINK"})
        admin = self.client.get(f"/api/admin/batches/{self.batch_id}/videos", headers=self.headers("boss")).json()
        self.assertEqual({v["source"] for v in admin}, {"RECORDING", "LINK"})
        with self.sf() as db:
            video_id = db.scalar(select(ClassVideo.id).where(ClassVideo.source == "RECORDING"))
        r = self.client.delete(f"/api/staff/me/batches/{self.batch_id}/videos/{video_id}", headers=self.headers("trainer"))
        self.assertEqual(r.status_code, 409)
        r = self.client.delete(f"/api/admin/batches/{self.batch_id}/videos/{video_id}", headers=self.headers("boss"))
        self.assertEqual(r.status_code, 409)
        self.assertEqual(self.count(ClassVideo, ClassVideo.source == "RECORDING"), 1)

    def test_newly_posted_links_must_be_web_links(self):
        for bad in ("javascript:alert(1)", "data:text/html,x", "file:///etc/passwd", "ftp://x"):
            with self.subTest(bad):
                r = self.client.post(f"/api/staff/me/batches/{self.batch_id}/videos", headers=self.headers("trainer"),
                                     json={"title": "x", "video_url": bad})
                self.assertEqual(r.status_code, 422)
        ok = self.client.post(f"/api/staff/me/batches/{self.batch_id}/videos", headers=self.headers("trainer"),
                              json={"title": "x", "video_url": "https://youtu.be/abc"})
        self.assertEqual(ok.status_code, 201, ok.text)

    def test_one_class_video_entry_per_recording_is_enforced_by_the_database(self):
        from sqlalchemy.exc import IntegrityError
        with self.sf() as db:
            db.add(ClassVideo(batch_id=self.batch_id, title="dup", source="RECORDING", recording_id=__import__("uuid").UUID(self.rid)))
            with self.assertRaises(IntegrityError):
                db.flush()
            db.rollback()
            db.add(ClassVideo(batch_id=self.batch_id, title="nothing"))  # neither a url nor a recording
            with self.assertRaises(IntegrityError):
                db.flush()


if __name__ == "__main__":
    unittest.main()
