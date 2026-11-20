"""Remote (OneDrive) playback, access control around it, and the retention safeguards.

Everything here uses a simulated Graph (httpx.MockTransport) and disposable temp
directories / SQLite. Nothing contacts Microsoft or touches real recordings.
"""
import hashlib
import os
import re
import tempfile
import unittest
import uuid
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

import httpx
from sqlalchemy import select

from app.config import Settings, settings
from app.core import job_handlers, recording_publish, recording_retention
from app.core.class_runs import utcnow
from app.core.job_queue import RetryLater
from app.core.onedrive_store import OneDriveError, OneDriveStore, quick_xor_hash_b64
from app.core.recording_storage import StagingStore
from app.models import AdminRole, BatchEnrollment, RoleEnum, User
from app.models.admin_role import SUPER_ADMIN_SLUG
from app.models.class_run import ClassRecording, RecordingEvent
from tests.test_job_queue_and_class_lifecycle import Harness
from tests.test_recording_pipeline_media import FakeGraph, make_mp4

ITEM = "REMOTE-ITEM-ID-123"
BEARER = "SECRET-GRAPH-BEARER-TOKEN"
DOWNLOAD_HOST = "https://download.example.test"
PREAUTH = f"{DOWNLOAD_HOST}/pre-auth/SECRET-DOWNLOAD-URL"


class FakeDrive:
    """Simulated Graph read side: item metadata + /content (redirecting, with Range)."""

    def __init__(self, data: bytes, *, meta_status=200, content_status=None, hash_override=None, size_override=None,
                 content_length_lie=None):
        self.data, self.requests, self.only_drive = data, [], None
        self.meta_status, self.content_status = meta_status, content_status
        self.hash_override, self.size_override, self.lie = hash_override, size_override, content_length_lie
        self.client = httpx.Client(transport=httpx.MockTransport(self.handle))

    def _hash(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            p = Path(tmp) / "x"
            p.write_bytes(self.data)
            return quick_xor_hash_b64(p)

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = str(request.url)
        if url.startswith(DOWNLOAD_HOST):
            return self._serve(request)
        m = re.search(r"/drives/([^/]+)/", url)
        if m and self.only_drive is not None and m.group(1) != self.only_drive:
            return httpx.Response(404)  # the item lives in one drive only
        if "/items/" in url and "/content" in url:
            if self.content_status:
                return httpx.Response(self.content_status)
            return httpx.Response(302, headers={"Location": PREAUTH})
        if "/items/" in url:
            if self.meta_status != 200:
                return httpx.Response(self.meta_status)
            size = len(self.data) if self.size_override is None else self.size_override
            return httpx.Response(200, json={"id": ITEM, "size": size, "file": {"hashes": {
                "quickXorHash": self.hash_override or self._hash()}}})
        return httpx.Response(500)

    def _serve(self, request):
        rng = request.headers.get("Range")
        if rng:
            a, b = re.match(r"bytes=(\d+)-(\d+)", rng).groups()
            a, b = int(a), int(b)
            body = self.data[a:b + 1]
            return httpx.Response(206, content=body, headers={
                "Content-Range": f"bytes {a}-{b}/{len(self.data)}",
                "Content-Length": str(len(body) if self.lie is None else self.lie)})
        return httpx.Response(200, content=self.data, headers={
            "Content-Length": str(len(self.data) if self.lie is None else self.lie)})

    def store(self, *, enabled=True, drive_id="DRIVE-1"):
        return OneDriveStore(drive_id=drive_id, token_provider=lambda: BEARER, http=self.client, enabled=enabled,
                             sleep=lambda s: None)

    def drives_used(self):
        return {m.group(1) for r in self.requests if (m := re.search(r"/drives/([^/]+)/", str(r.url)))}

    def store_class(self):
        """OneDriveStore subclass wired to this fake Graph, so the REAL drive-selection code runs."""
        drive = self

        class Wired(OneDriveStore):
            def __init__(self, **kw):
                super().__init__(token_provider=lambda: BEARER, http=drive.client, enabled=True,
                                 sleep=lambda s: None, **kw)
        return Wired

    def graph_requests(self):
        return [r for r in self.requests if "graph.microsoft.com" in str(r.url)]


class Base(Harness):
    def setUp(self):
        super().setUp()
        self.run_id = self.make_run(state="ENDED", approval="APPROVED", rec="PUBLISHED")
        (self.storage / "incoming").mkdir(exist_ok=True)
        self.key = f"incoming/{os.urandom(8).hex()}.mp4"
        self.path = self.storage / self.key
        make_mp4(self.path, 3)
        self.data = self.path.read_bytes()
        self.sha = hashlib.sha256(self.data).hexdigest()
        self.drive = FakeDrive(self.data)
        with self.sf() as db:
            rec = ClassRecording(
                run_id=self.run_id, batch_id=self.batch_id, capture_state="STORED", publication_state="PUBLISHED",
                staging_key=self.key, size_bytes=len(self.data), sha256=self.sha, duration_seconds=3,
                verified_at=utcnow(), published_at=utcnow(), storage_provider="ONEDRIVE", remote_item_id=ITEM,
                remote_drive_id="DRIVE-1", remote_verified_at=utcnow() - timedelta(days=60))
            db.add(rec)
            sa = AdminRole(name="Super Admin", slug=SUPER_ADMIN_SLUG, is_system=True)
            db.add(sa)
            db.flush()
            db.add(User(username="boss", full_name="Boss", role=RoleEnum.admin, admin_role_id=sa.id, password_hash="x",
                        is_active=True, must_change_password=False))
            db.commit()
            self.rid = str(rec.id)
        with self.sf() as db:
            self.ids["boss"] = db.scalar(select(User.id).where(User.username == "boss"))
        self._patch_store(self.drive.store())

    def _patch_store(self, store):
        p = patch("app.api.routes_recordings._new_store", lambda drive_id=None: store)
        p.start()
        self.addCleanup(p.stop)

    def stream_url(self, who="student"):
        r = self.client.post(f"/api/recordings/{self.rid}/playback-token", headers=self.headers(who))
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()["stream_url"]

    def tok(self, who="student"):
        return self.stream_url(who).split("t=")[1]

    def stream(self, t, method="get", **kw):
        return getattr(self.client, method)(f"/api/recordings/{self.rid}/stream", params={"t": t}, **kw)

    def remove_local(self):
        self.path.unlink()

    def rec(self):
        with self.sf() as db:
            return db.get(ClassRecording, uuid.UUID(self.rid))

    def set_rec(self, **fields):
        with self.sf() as db:
            r = db.get(ClassRecording, uuid.UUID(self.rid))
            for k, v in fields.items():
                setattr(r, k, v)
            db.commit()

    def events(self):
        with self.sf() as db:
            return [e.event for e in db.scalars(select(RecordingEvent).where(RecordingEvent.recording_id == uuid.UUID(self.rid)))]


# ---------------------------------------------------------------------------
class RemotePlaybackTests(Base):
    def test_playback_falls_back_to_onedrive_when_the_local_copy_is_gone(self):
        t = self.tok()
        self.remove_local()
        full = self.stream(t)
        self.assertEqual((full.status_code, full.content), (200, self.data))
        part = self.stream(t, headers={"Range": "bytes=10-99"})
        self.assertEqual((part.status_code, part.content), (206, self.data[10:100]))
        self.assertEqual(part.headers["content-range"], f"bytes 10-99/{len(self.data)}")
        tail = self.stream(t, headers={"Range": "bytes=-50"})
        self.assertEqual((tail.status_code, tail.content), (206, self.data[-50:]))

    def test_nothing_about_onedrive_or_graph_reaches_the_browser(self):
        t = self.tok()
        self.remove_local()
        for r in (self.stream(t), self.stream(t, headers={"Range": "bytes=0-9"})):
            self.assertIn(r.status_code, (200, 206))  # never a redirect to Microsoft
            blob = " ".join(f"{k}:{v}" for k, v in r.headers.items())
            for secret in (BEARER, ITEM, PREAUTH, "graph.microsoft.com", "download.example.test", "SECRET-DOWNLOAD", self.key, "DRIVE-1"):
                self.assertNotIn(secret, blob)
            self.assertNotIn("location", r.headers)
            self.assertIn("no-store", r.headers["cache-control"])
            self.assertEqual(r.headers["content-type"], "video/mp4")
            self.assertNotIn("attachment", r.headers.get("content-disposition", ""))
        url = self.stream_url()
        self.assertTrue(url.startswith(f"/api/recordings/{self.rid}/stream?t="))
        self.assertNotIn(ITEM, url)
        self.assertNotIn("DRIVE-1", url)

    def test_the_bearer_goes_only_to_graph_never_to_the_pre_authenticated_download_host(self):
        t = self.tok()
        self.remove_local()
        self.stream(t)
        for r in self.drive.requests:
            if str(r.url).startswith(DOWNLOAD_HOST):
                self.assertNotIn("authorization", r.headers)
            else:
                self.assertEqual(r.headers.get("authorization"), f"Bearer {BEARER}")

    def test_the_local_copy_is_used_when_present_without_touching_graph(self):
        r = self.stream(self.tok())
        self.assertEqual(r.content, self.data)
        self.assertEqual(self.drive.requests, [])

    def test_a_changed_local_file_is_not_served_the_remote_copy_is(self):
        t = self.tok()
        self.path.write_bytes(self.data[:-5])  # size no longer matches the verified record
        r = self.stream(t)
        self.assertEqual(r.content, self.data)
        self.assertTrue(self.drive.requests)

    def test_head_never_contacts_onedrive(self):
        t = self.tok()
        self.remove_local()
        r = self.stream(t, "head")
        self.assertEqual((r.status_code, r.content), (200, b""))
        self.assertEqual(int(r.headers["content-length"]), len(self.data))
        self.assertEqual(self.drive.requests, [])

    def test_unsatisfiable_range_is_refused_without_contacting_onedrive(self):
        t = self.tok()
        self.remove_local()
        r = self.stream(t, headers={"Range": f"bytes={len(self.data) + 9}-"})
        self.assertEqual(r.status_code, 416)
        self.assertEqual(self.drive.requests, [])

    def test_turning_uploads_off_does_not_stop_playback_of_stored_recordings(self):
        self._patch_store(self.drive.store(enabled=False))
        t = self.tok()
        self.remove_local()
        self.assertEqual(self.stream(t).content, self.data)


class RemoteAccessControlTests(Base):
    def test_only_enrolled_students_and_the_batch_trainer_can_play_from_onedrive(self):
        self.remove_local()
        for who in ("student", "trainer"):
            with self.subTest(who):
                self.assertEqual(self.stream(self.tok(who)).content, self.data)

    def test_admins_super_admins_and_unrelated_users_get_nothing_and_cause_no_onedrive_traffic(self):
        self.remove_local()
        for who in ("boss", "outsider", "other-trainer"):
            with self.subTest(who):
                r = self.client.post(f"/api/recordings/{self.rid}/playback-token", headers=self.headers(who))
                self.assertEqual(r.status_code, 404)
        import jwt
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        for who in ("boss", "outsider", "other-trainer"):
            with self.subTest(f"forged {who}"):
                forged = jwt.encode({"purpose": "recording-playback", "rid": self.rid, "sub": str(self.ids[who]),
                                     "iat": now, "exp": now + timedelta(minutes=5)}, settings.jwt_secret, algorithm="HS256")
                self.assertEqual(self.stream(forged).status_code, 404)
        self.assertEqual(self.client.get(f"/api/recordings/{self.rid}/stream").status_code, 422)
        self.assertEqual(self.drive.requests, [])

    def test_a_removed_student_loses_remote_playback_immediately(self):
        t = self.tok("student")
        self.remove_local()
        self.assertEqual(self.stream(t).status_code, 200)
        with self.sf() as db:
            for row in db.scalars(select(BatchEnrollment).where(BatchEnrollment.student_id == self.ids["student"])):
                db.delete(row)
            db.commit()
        before = len(self.drive.requests)
        self.assertEqual(self.stream(t).status_code, 404)
        self.assertEqual(len(self.drive.requests), before)

    def test_unpublished_or_deleted_recordings_are_never_fetched_from_onedrive(self):
        t = self.tok()
        self.remove_local()
        for change in ({"publication_state": "REVOKED"}, {"deleted_at": utcnow()}):
            with self.subTest(change):
                self.set_rec(publication_state="PUBLISHED", deleted_at=None)
                self.set_rec(**change)
                self.assertEqual(self.stream(t).status_code, 404)
        self.assertEqual(self.drive.requests, [])

    def test_there_is_still_no_download_route(self):
        from app.main import app
        for route in app.routes:
            if getattr(route, "path", "").startswith("/api/recordings"):
                self.assertNotIn("download", route.path.lower())
                self.assertFalse({"DELETE", "PUT", "PATCH"} & set(route.methods or ()))


class LogLeakTests(Base):
    def test_no_credential_or_remote_url_is_logged_even_with_info_logging_on(self):
        import logging
        from app.core import log_redaction
        log_redaction.install()
        root = logging.getLogger()
        old = root.level
        root.setLevel(logging.DEBUG)
        try:
            with self.assertLogs(level=logging.DEBUG) as captured:
                logging.getLogger("probe").info("probe")  # assertLogs needs at least one record
                t = self.tok()
                self.remove_local()
                self.stream(t)
                self.stream(t, headers={"Range": "bytes=0-9"})
                self.drive.content_status = 500
                self.stream(t)
                self.drive.content_status = None
                self.run_ret_logged()
        finally:
            root.setLevel(old)
        blob = " | ".join(captured.output)
        for secret in (BEARER, PREAUTH, "SECRET-DOWNLOAD", "download.example.test", t):
            self.assertNotIn(secret, blob)

    def run_ret_logged(self):
        with self.sf() as db:
            recording_retention.run_retention(db, store_factory=lambda d: self.drive.store(), enabled=True, dry_run=True)

    def test_httpx_loggers_are_held_at_warning(self):
        import logging
        from app.core import log_redaction
        log_redaction.install()
        for name in ("httpx", "httpcore"):
            self.assertGreaterEqual(logging.getLogger(name).level, logging.WARNING)


class RemoteFailureTests(Base):
    def _expect_503(self, r):
        self.assertEqual(r.status_code, 503)
        blob = r.text + str(dict(r.headers))
        for secret in (BEARER, ITEM, PREAUTH, "graph.microsoft.com", self.key, "Traceback", "DRIVE-1"):
            self.assertNotIn(secret, blob)

    def test_missing_everywhere_fails_with_a_generic_503(self):
        t = self.tok()
        self.remove_local()
        self.set_rec(remote_item_id=None)
        self._expect_503(self.stream(t))

    def test_each_missing_remote_precondition_fails_closed(self):
        t = self.tok()
        self.remove_local()
        for change in ({"remote_verified_at": None}, {"storage_provider": "LOCAL_STAGING"}, {"size_bytes": None}, {"size_bytes": 0}):
            with self.subTest(change):
                self.set_rec(remote_item_id=ITEM, remote_verified_at=utcnow(), storage_provider="ONEDRIVE", size_bytes=len(self.data))
                self.set_rec(**change)
                self._expect_503(self.stream(t))
        self.assertEqual(self.drive.requests, [])

    def test_onedrive_not_configured_fails_closed(self):
        self._patch_store(self.drive.store(drive_id=""))
        t = self.tok()
        self.remove_local()
        self._expect_503(self.stream(t))
        self.assertEqual(self.drive.requests, [])

    def test_graph_errors_become_a_generic_503(self):
        t = self.tok()
        self.remove_local()
        for status in (401, 403, 404, 429, 500, 503):
            with self.subTest(status):
                self.drive.content_status = status
                self._expect_503(self.stream(t))
                self._expect_503(self.stream(t, headers={"Range": "bytes=0-9"}))

    def test_a_token_failure_is_a_generic_503_that_leaks_nothing(self):
        def boom():
            raise OneDriveError(f"could not get token {BEARER}")
        self._patch_store(OneDriveStore(drive_id="DRIVE-1", token_provider=boom, http=self.drive.client, enabled=True))
        t = self.tok()
        self.remove_local()
        self._expect_503(self.stream(t))

    def test_a_network_failure_is_a_generic_503(self):
        def refuse(request):
            raise httpx.ConnectError("down", request=request)
        client = httpx.Client(transport=httpx.MockTransport(refuse))
        self._patch_store(OneDriveStore(drive_id="DRIVE-1", token_provider=lambda: BEARER, http=client, enabled=True))
        t = self.tok()
        self.remove_local()
        self._expect_503(self.stream(t))

    def test_a_remote_file_whose_size_changed_is_refused_not_served(self):
        t = self.tok()
        self.remove_local()
        self.drive.lie = len(self.data) - 7  # remote claims a different length than the verified record
        self._expect_503(self.stream(t))
        self._expect_503(self.stream(t, headers={"Range": "bytes=0-9"}))

    def test_a_local_file_that_vanished_while_remote_is_down_is_503_not_a_crash(self):
        t = self.tok()
        self.remove_local()
        self.drive.content_status = 500
        self._expect_503(self.stream(t))


# ---------------------------------------------------------------------------
# Upload integrity -> a local copy is only ever "safe to remove" after verification
# ---------------------------------------------------------------------------
class UploadIntegrityGateTests(Harness):
    def test_a_failed_or_tampered_upload_never_marks_the_remote_copy_verified(self):
        run_id = self.make_run(state="ENDED", approval="APPROVED", rec="PUBLISHED")
        (self.storage / "incoming").mkdir(exist_ok=True)
        key = f"incoming/{os.urandom(8).hex()}.mp4"
        path = self.storage / key
        make_mp4(path, 3)
        with self.sf() as db:
            rec = ClassRecording(run_id=run_id, batch_id=self.batch_id, capture_state="STORED",
                                 publication_state="NOT_PUBLISHED", staging_key=key, size_bytes=path.stat().st_size,
                                 sha256=hashlib.sha256(path.read_bytes()).hexdigest(), duration_seconds=3, verified_at=utcnow())
            db.add(rec)
            db.commit()
            rid = str(rec.id)
        for graph in (FakeGraph(tamper=True), FakeGraph(wrong_size=True), FakeGraph(expire_session=True)):
            store = OneDriveStore(drive_id="D", token_provider=lambda: "t", http=graph.client, enabled=True, sleep=lambda s: None)
            with patch.object(recording_publish, "OneDriveStore", lambda s=store: s):
                with self.sf() as db:
                    with self.assertRaises(RetryLater):
                        recording_publish.upload_recording(db, rid)
            with self.sf() as db:
                row = db.get(ClassRecording, uuid.UUID(rid))
                self.assertIsNone(row.remote_verified_at)
                self.assertIsNone(row.remote_item_id)
                self.assertIsNone(row.remote_drive_id)
            self.assertTrue(path.is_file())
            # and retention (even fully enabled and applying) keeps the file
            with self.sf() as db:
                rep = recording_retention.run_retention(db, store_factory=lambda d: store, enabled=True, dry_run=False)
            self.assertEqual(rep["removed"], 0)
            self.assertTrue(path.is_file())


# ---------------------------------------------------------------------------
class RetentionTests(Base):
    def run_ret(self, *, drive=None, enabled=True, dry_run=False, **kw):
        store = (drive or self.drive).store()
        with self.sf() as db:
            return recording_retention.run_retention(db, store_factory=lambda d: store, enabled=enabled, dry_run=dry_run, **kw)

    # -- defaults ---------------------------------------------------------------------
    def test_cleanup_is_disabled_and_dry_run_by_default(self):
        defaults = Settings.model_fields
        self.assertIs(defaults["recording_retention_enabled"].default, False)
        self.assertIs(defaults["recording_retention_dry_run"].default, True)

    def test_disabled_means_nothing_is_examined_or_removed(self):
        rep = self.run_ret(enabled=False)
        self.assertEqual((rep["examined"], rep["removed"]), (0, 0))
        self.assertTrue(self.path.is_file())
        self.assertEqual(self.drive.requests, [])

    def test_settings_default_path_does_nothing(self):
        with self.sf() as db:
            rep = recording_retention.run_retention(db, store_factory=lambda d: self.drive.store())  # real defaults
        self.assertFalse(rep["enabled"])
        self.assertTrue(self.path.is_file())

    def test_dry_run_reports_but_removes_nothing(self):
        rep = self.run_ret(dry_run=True)
        self.assertEqual((rep["would_remove"], rep["removed"]), (1, 0))
        self.assertTrue(self.path.is_file())
        self.assertNotIn("local_copy_removed", self.events())

    # -- the happy path ----------------------------------------------------------------------
    def test_a_verified_old_copy_is_removed_and_playback_continues_from_onedrive(self):
        neighbour = self.storage / "incoming" / "neighbour.mp4"
        neighbour.write_bytes(b"other")
        t = self.tok()
        rep = self.run_ret()
        self.assertEqual((rep["removed"], rep["kept"]), (1, 0))
        self.assertFalse(self.path.exists())
        self.assertTrue(neighbour.is_file())            # only that one file
        self.assertTrue((self.storage / "incoming").is_dir())
        self.assertIn("local_copy_removed", self.events())
        row = self.rec()                                # record, remote pointers and publication untouched
        self.assertEqual((row.publication_state, row.remote_item_id, row.deleted_at), ("PUBLISHED", ITEM, None))
        self.assertEqual(self.stream(t).content, self.data)

    def test_retention_never_deletes_remote_files(self):
        self.run_ret()
        self.assertTrue(all(r.method in ("GET",) for r in self.drive.requests))

    def test_a_second_run_is_a_harmless_no_op(self):
        self.run_ret()
        rep = self.run_ret()
        self.assertEqual(rep["removed"], 0)

    # -- every safeguard keeps the file -------------------------------------------------------
    def assertKept(self, rep=None, reason=None):
        rep = rep or self.run_ret()
        self.assertEqual(rep["removed"], 0, rep)
        self.assertTrue(self.path.is_file(), "the local copy must be kept")
        if reason:
            self.assertIn(reason, rep["kept_reasons"], rep)

    def test_remote_content_mismatch_keeps_the_local_copy(self):
        self.assertKept(self.run_ret(drive=FakeDrive(self.data, hash_override="AAAAAAAAAAAAAAAAAAAAAAAAAAA="), ), "remote_unverified")

    def test_remote_size_mismatch_keeps_the_local_copy(self):
        self.assertKept(self.run_ret(drive=FakeDrive(self.data, size_override=len(self.data) + 1)), "remote_unverified")

    def test_remote_with_no_hash_yet_keeps_the_local_copy(self):
        class NoHash(FakeDrive):
            def handle(self, request):
                if "/items/" in str(request.url) and "/content" not in str(request.url):
                    return httpx.Response(200, json={"id": ITEM, "size": len(self.data), "file": {}})
                return super().handle(request)
        self.assertKept(self.run_ret(drive=NoHash(self.data)), "remote_unverified")

    def test_remote_failures_keep_the_local_copy(self):
        for status in (401, 403, 404, 429, 500, 503):
            with self.subTest(status):
                self.assertKept(self.run_ret(drive=FakeDrive(self.data, meta_status=status)), "remote_unverified")

    def test_unreadable_or_unconfigured_onedrive_keeps_the_local_copy(self):
        store = OneDriveStore(drive_id="", token_provider=lambda: "t", http=self.drive.client, enabled=True)
        with self.sf() as db:
            rep = recording_retention.run_retention(db, store_factory=lambda d: store, enabled=True, dry_run=False)
        self.assertKept(rep, "remote_unverified")

    def test_an_unexpected_error_keeps_the_local_copy_and_does_not_abort_the_pass(self):
        class Exploding:
            def verify_remote(self, *a, **k):
                raise RuntimeError("boom")
        with self.sf() as db:
            rep = recording_retention.run_retention(db, store_factory=lambda d: Exploding(), enabled=True, dry_run=False)
        self.assertKept(rep, "error")

    def test_a_corrupt_or_changed_local_file_is_kept(self):
        self.path.write_bytes(self.data[:-1])
        self.assertKept(reason="local_size_mismatch")
        self.path.write_bytes(bytes([self.data[0] ^ 1]) + self.data[1:])  # same size, different content
        self.assertKept(reason="local_hash_mismatch")

    def test_recordings_without_a_verified_remote_copy_are_kept(self):
        for change in ({"remote_verified_at": None}, {"remote_item_id": None}, {"remote_item_id": ""},
                       {"storage_provider": "LOCAL_STAGING"}, {"storage_provider": None}):
            with self.subTest(change):
                self.set_rec(remote_item_id=ITEM, remote_verified_at=utcnow() - timedelta(days=60), storage_provider="ONEDRIVE")
                self.set_rec(**change)
                self.assertKept()
        self.assertEqual(self.drive.requests, [])

    def test_missing_integrity_record_keeps_the_file(self):
        for change in ({"sha256": None}, {"size_bytes": None}, {"staging_key": None}):
            with self.subTest(change):
                self.set_rec(sha256=self.sha, size_bytes=len(self.data), staging_key=self.key)
                self.set_rec(**change)
                self.assertKept()

    def test_unpublished_failed_or_deleted_recordings_are_kept(self):
        for change in ({"publication_state": "NOT_PUBLISHED"}, {"publication_state": "PUBLISH_FAILED"}, {"publication_state": "PUBLISHING"}, {"publication_state": "REVOKED"},
                       {"capture_state": "FAILED"}, {"deleted_at": utcnow()}):
            with self.subTest(change):
                self.set_rec(publication_state="PUBLISHED", capture_state="STORED", deleted_at=None)
                self.set_rec(**change)
                self.assertKept()
        self.assertEqual(self.drive.requests, [])

    def test_recent_remote_copies_are_kept_and_the_minimum_age_floor_cannot_be_configured_away(self):
        self.set_rec(remote_verified_at=utcnow() - timedelta(days=3))
        with patch.object(settings, "recording_retention_local_days", 0):
            self.assertEqual(recording_retention.effective_days(), recording_retention.RETENTION_MIN_DAYS)
            self.assertKept()
        with patch.object(settings, "recording_retention_local_days", -5):
            self.assertKept()
        with patch.object(settings, "recording_retention_local_days", 90):  # 60 days old < 90
            self.assertKept()
        self.set_rec(remote_verified_at=utcnow() - timedelta(days=60))
        with patch.object(settings, "recording_retention_local_days", 30):
            self.assertEqual(self.run_ret()["removed"], 1)

    def test_a_staging_key_that_escapes_the_root_never_touches_an_outside_file(self):
        outside = Path(self.temp.name) / "outside.mp4"
        outside.write_bytes(self.data)
        for evil in ("../outside.mp4", "/" + str(outside), "incoming/../../outside.mp4", "..\\outside.mp4"):
            with self.subTest(evil):
                self.set_rec(staging_key=evil)
                rep = self.run_ret()
                self.assertEqual(rep["removed"], 0)
                self.assertTrue(outside.is_file())
                self.assertTrue(self.path.is_file())

    def test_a_directory_or_missing_file_is_never_removed(self):
        self.path.unlink()
        self.set_rec(staging_key="incoming")
        rep = self.run_ret()
        self.assertEqual(rep["removed"], 0)
        self.assertTrue((self.storage / "incoming").is_dir())
        self.set_rec(staging_key=self.key)
        rep = self.run_ret()  # file already absent: nothing to do, no error
        self.assertEqual((rep["removed"], rep["kept_reasons"].get("already_absent")), (0, 1))

    def test_the_batch_limit_bounds_one_pass(self):
        with self.sf() as db:
            for i in range(4):
                key = f"incoming/extra{i}.mp4"
                (self.storage / key).write_bytes(self.data)
                run_id = self.make_run(state="ENDED", approval="APPROVED", rec="PUBLISHED")
                db.add(ClassRecording(run_id=run_id, batch_id=self.batch_id, capture_state="STORED", publication_state="PUBLISHED",
                                      staging_key=key, size_bytes=len(self.data), sha256=self.sha, verified_at=utcnow(),
                                      storage_provider="ONEDRIVE", remote_item_id=ITEM,
                                      remote_verified_at=utcnow() - timedelta(days=60)))
            db.commit()
        with patch.object(settings, "recording_retention_batch_limit", 2):
            rep = self.run_ret()
        self.assertEqual(rep["examined"], 2)
        self.assertLessEqual(rep["removed"], 2)

    def test_the_retention_policy_never_runs_from_the_scheduler_unless_enabled(self):
        calls = []
        with patch.object(recording_retention, "run_retention", lambda db, **k: calls.append(1)), \
                patch.object(job_handlers.lifecycle, "reconcile_sessions", lambda db: None), \
                patch.object(job_handlers.recording_publish, "requeue_unpublished", lambda db: 0), \
                patch.object(job_handlers.recording_watchdog, "check_recordings", lambda db: {}):
            with patch.object(settings, "recording_retention_enabled", False):
                job_handlers._reconcile(None, None)
            self.assertEqual(calls, [])
            with patch.object(settings, "recording_retention_enabled", True):
                job_handlers._reconcile(None, None)
            self.assertEqual(calls, [1])

    def test_a_retention_crash_cannot_break_the_scheduler(self):
        def crash(db, **k):
            raise RuntimeError("x")
        class _DB:
            def rollback(self):
                pass
        with patch.object(recording_retention, "run_retention", crash), \
                patch.object(job_handlers.lifecycle, "reconcile_sessions", lambda db: None), \
                patch.object(job_handlers.recording_publish, "requeue_unpublished", lambda db: 0), \
                patch.object(job_handlers.recording_watchdog, "check_recordings", lambda db: {}), \
                patch.object(settings, "recording_retention_enabled", True):
            job_handlers._reconcile(_DB(), None)  # must not raise


# ---------------------------------------------------------------------------
# Each recording remembers the drive it was uploaded to
# ---------------------------------------------------------------------------
class DriveIdTests(Base):
    """Uses the REAL drive-selection code (a store subclass wired to the fake Graph),
    so changing ONEDRIVE_DRIVE_ID after upload is exercised end to end."""

    def setUp(self):
        super().setUp()
        self.drive.only_drive = "DRIVE-1"           # the item exists in that drive only
        wired = self.drive.store_class()
        for target in ("app.api.routes_recordings.OneDriveStore", "app.core.recording_retention.OneDriveStore"):
            p = patch(target, wired)
            p.start()
            self.addCleanup(p.stop)
        # un-patch the route's factory seam so the real _new_store runs against the wired class
        p = patch("app.api.routes_recordings._new_store", lambda drive_id=None: wired(drive_id=drive_id or None))
        p.start()
        self.addCleanup(p.stop)

    def ret(self, **kw):
        with self.sf() as db:
            return recording_retention.run_retention(db, enabled=True, dry_run=False, **kw)

    def test_playback_uses_the_recorded_drive_after_the_configured_drive_changes(self):
        t = self.tok()
        self.remove_local()
        with patch.object(settings, "onedrive_drive_id", "NEW-DRIVE"):
            self.assertEqual(self.stream(t).content, self.data)
            self.assertEqual(self.stream(t, headers={"Range": "bytes=5-20"}).content, self.data[5:21])
        self.assertEqual(self.drive.drives_used(), {"DRIVE-1"})

    def test_playback_works_even_if_no_drive_is_configured_any_more(self):
        t = self.tok()
        self.remove_local()
        with patch.object(settings, "onedrive_drive_id", ""):
            self.assertEqual(self.stream(t).content, self.data)

    def test_a_recorded_drive_is_never_silently_swapped_for_the_configured_one(self):
        self.set_rec(remote_drive_id="DRIVE-GONE")
        t = self.tok()
        self.remove_local()
        with patch.object(settings, "onedrive_drive_id", "DRIVE-1"):   # configured drive DOES have the item
            r = self.stream(t)
        self.assertEqual(r.status_code, 503)
        self.assertEqual(self.drive.drives_used(), {"DRIVE-GONE"})

    def test_legacy_rows_without_a_recorded_drive_keep_using_the_configured_drive(self):
        self.set_rec(remote_drive_id=None)
        t = self.tok()
        self.remove_local()
        with patch.object(settings, "onedrive_drive_id", "DRIVE-1"):
            self.assertEqual(self.stream(t).content, self.data)
        with patch.object(settings, "onedrive_drive_id", "WRONG"):
            self.assertEqual(self.stream(t).status_code, 503)           # same behaviour as before this column
        with patch.object(settings, "onedrive_drive_id", ""):
            self.assertEqual(self.stream(t).status_code, 503)
        self.assertIsNone(self.rec().remote_drive_id)                   # reads never write the column (no backfill)

    def test_access_rules_are_unchanged_for_recordings_with_a_recorded_drive(self):
        self.remove_local()
        for who in ("student", "trainer"):
            self.assertEqual(self.stream(self.tok(who)).status_code, 200)
        for who in ("boss", "outsider", "other-trainer"):
            r = self.client.post(f"/api/recordings/{self.rid}/playback-token", headers=self.headers(who))
            self.assertEqual(r.status_code, 404)
            self.assertNotIn("DRIVE-1", r.text)

    def test_retention_verifies_against_the_recorded_drive_after_a_config_change(self):
        with patch.object(settings, "onedrive_drive_id", "NEW-DRIVE"):
            rep = self.ret()
        self.assertEqual(rep["removed"], 1)
        self.assertEqual(self.drive.drives_used(), {"DRIVE-1"})

    def test_retention_keeps_the_file_if_the_recorded_drive_no_longer_has_it(self):
        self.set_rec(remote_drive_id="DRIVE-GONE")
        with patch.object(settings, "onedrive_drive_id", "DRIVE-1"):    # must not fall back to the configured drive
            rep = self.ret()
        self.assertEqual(rep["removed"], 0)
        self.assertTrue(self.path.is_file())
        self.assertEqual(self.drive.drives_used(), {"DRIVE-GONE"})

    def test_retention_on_legacy_rows_uses_the_configured_drive_and_fails_safe_if_it_changed(self):
        self.set_rec(remote_drive_id=None)
        with patch.object(settings, "onedrive_drive_id", "WRONG"):
            self.assertEqual(self.ret()["removed"], 0)
        self.assertTrue(self.path.is_file())
        with patch.object(settings, "onedrive_drive_id", ""):
            self.assertEqual(self.ret()["removed"], 0)
        self.assertTrue(self.path.is_file())
        with patch.object(settings, "onedrive_drive_id", "DRIVE-1"):
            self.assertEqual(self.ret()["removed"], 1)

    def test_nothing_ever_writes_remote_drive_id_except_a_verified_upload(self):
        self.set_rec(remote_drive_id=None)
        t = self.tok()
        with patch.object(settings, "onedrive_drive_id", "DRIVE-1"):
            self.stream(t)
            self.remove_local()
            self.stream(t)
            self.ret()
        self.assertIsNone(self.rec().remote_drive_id)


class UploadRecordsTheDriveTests(Harness):
    def _recording(self):
        run_id = self.make_run(state="ENDED", approval="APPROVED", rec="PUBLISHED")
        (self.storage / "incoming").mkdir(exist_ok=True)
        key = f"incoming/{os.urandom(8).hex()}.mp4"
        path = self.storage / key
        make_mp4(path, 3)
        with self.sf() as db:
            rec = ClassRecording(run_id=run_id, batch_id=self.batch_id, capture_state="STORED",
                                 publication_state="NOT_PUBLISHED", staging_key=key, size_bytes=path.stat().st_size,
                                 sha256=hashlib.sha256(path.read_bytes()).hexdigest(), duration_seconds=3, verified_at=utcnow())
            db.add(rec)
            db.commit()
            return str(rec.id)

    def test_a_verified_upload_records_the_drive_it_went_to_and_later_config_changes_do_not_alter_it(self):
        rid = self._recording()
        graph = FakeGraph()

        class Wired(OneDriveStore):
            def __init__(self, **kw):
                super().__init__(token_provider=lambda: "t", http=graph.client, enabled=True, sleep=lambda s: None,
                                 drive_id="UPLOAD-DRIVE")
        with patch.object(recording_publish, "OneDriveStore", Wired):
            with self.sf() as db:
                self.assertEqual(recording_publish.upload_recording(db, rid), "uploaded")
        with self.sf() as db:
            row = db.get(ClassRecording, uuid.UUID(rid))
            self.assertEqual((row.remote_drive_id, row.storage_provider), ("UPLOAD-DRIVE", "ONEDRIVE"))
            self.assertIsNotNone(row.remote_verified_at)
        self.assertTrue(any("/drives/UPLOAD-DRIVE/" in str(r.url) for r in graph.requests))
        with patch.object(settings, "onedrive_drive_id", "SOMETHING-ELSE"):
            with self.sf() as db:
                self.assertEqual(db.get(ClassRecording, uuid.UUID(rid)).remote_drive_id, "UPLOAD-DRIVE")


# ---------------------------------------------------------------------------
# Migration compatibility. SQLite here; the real Alembic chain is run on a
# disposable PostgreSQL separately (see the verification report).
# ---------------------------------------------------------------------------
class MigrationCompatTests(unittest.TestCase):
    REV = "f1a7c3d9b254"

    def _load(self):
        import importlib.util
        path = Path(__file__).resolve().parents[1] / "alembic" / "versions" / f"{self.REV}_add_class_recordings_remote_drive_id.py"
        spec = importlib.util.spec_from_file_location("mig_remote_drive", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_revision_follows_the_previous_head_and_is_the_only_head(self):
        import ast
        module = self._load()
        self.assertEqual((module.revision, module.down_revision), (self.REV, "e9c5b1a8d236"))
        versions = Path(__file__).resolve().parents[1] / "alembic" / "versions"
        downs, revs = set(), set()
        for f in versions.glob("*.py"):
            for node in ast.parse(f.read_text(encoding="utf-8")).body:
                t = node.targets[0] if isinstance(node, ast.Assign) else getattr(node, "target", None)
                if isinstance(t, ast.Name) and t.id == "revision":
                    revs.add(ast.literal_eval(node.value))
                if isinstance(t, ast.Name) and t.id == "down_revision" and node.value is not None:
                    v = ast.literal_eval(node.value)
                    downs.update([] if v is None else [v] if isinstance(v, str) else v)
        self.assertEqual(revs - downs, {self.REV})

    def test_upgrade_adds_a_nullable_column_keeps_legacy_rows_null_and_downgrade_removes_only_it(self):
        from alembic.migration import MigrationContext
        from alembic.operations import Operations
        from sqlalchemy import create_engine, inspect, text
        from sqlalchemy.dialects.postgresql import JSONB
        from sqlalchemy.ext.compiler import compiles
        from app.database import Base
        compiles(JSONB, "sqlite")(lambda t, c, **kw: "JSON")
        module = self._load()
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            engine = create_engine(f"sqlite:///{Path(tmp) / 'm.sqlite'}")
            Base.metadata.create_all(engine)
            with engine.begin() as c:                      # recreate the PRE-migration shape
                c.execute(text("ALTER TABLE class_recordings DROP COLUMN remote_drive_id"))

            def cols():
                return {col["name"] for col in inspect(engine).get_columns("class_recordings")}
            self.assertNotIn("remote_drive_id", cols())
            with engine.begin() as c:
                c.execute(text("PRAGMA foreign_keys=OFF"))
                c.execute(text("INSERT INTO class_recordings (id, run_id, batch_id, attempt_no, capture_state, publication_state,"
                               " retry_count, storage_provider, remote_item_id) VALUES ('legacy-1', 1, 1, 1, 'STORED',"
                               " 'PUBLISHED', 0, 'ONEDRIVE', 'OLD-ITEM')"))
            with engine.begin() as c:
                with Operations.context(MigrationContext.configure(c)):
                    module.upgrade()
            info = {col["name"]: col for col in inspect(engine).get_columns("class_recordings")}
            self.assertTrue(info["remote_drive_id"]["nullable"])
            self.assertIsNone(info["remote_drive_id"]["default"])
            with engine.connect() as c:                     # nothing was backfilled
                row = c.execute(text("SELECT remote_item_id, remote_drive_id FROM class_recordings WHERE id='legacy-1'")).one()
            self.assertEqual(tuple(row), ("OLD-ITEM", None))
            with engine.begin() as c:
                with Operations.context(MigrationContext.configure(c)):
                    module.downgrade()
            self.assertNotIn("remote_drive_id", cols())
            self.assertIn("remote_item_id", cols())          # only the new column was dropped
            with engine.connect() as c:
                self.assertEqual(c.execute(text("SELECT count(*) FROM class_recordings")).scalar(), 1)
            with engine.begin() as c:                        # and it can be re-applied
                with Operations.context(MigrationContext.configure(c)):
                    module.upgrade()
            self.assertIn("remote_drive_id", cols())
            engine.dispose()


# ---------------------------------------------------------------------------
class PortalCompatibilityTests(unittest.TestCase):
    """The portal's OneDrive file storage shares OneDriveStore: the additions are
    read-only helpers and must not change its upload/readiness semantics."""

    def test_upload_readiness_is_unchanged_and_independent_of_read_readiness(self):
        s = OneDriveStore(drive_id="D", token_provider=lambda: "t", enabled=False)
        self.assertFalse(s.readiness()[0])        # uploads still off...
        self.assertTrue(s.read_readiness()[0])    # ...but reading stored objects is possible
        self.assertFalse(OneDriveStore(drive_id="", token_provider=lambda: "t", enabled=True).read_readiness()[0])
        self.assertTrue(OneDriveStore(drive_id="D", token_provider=lambda: "t", enabled=True).readiness()[0])

    def test_portal_document_store_still_honours_only_its_own_switch(self):
        from app.core import portal_storage
        with patch.object(settings, "portal_onedrive_enabled", False), patch.object(settings, "onedrive_upload_enabled", True), \
                patch.object(settings, "onedrive_drive_id", "D"):
            self.assertFalse(portal_storage.document_store().readiness()[0])

    def test_retention_and_playback_code_never_call_a_graph_write_or_delete(self):
        src = (Path(recording_retention.__file__).read_text(encoding="utf-8")
               + Path(job_handlers.__file__).read_text(encoding="utf-8"))
        self.assertNotRegex(src, r"\.delete\(|client\.(put|post|patch)\(|DELETE")


if __name__ == "__main__":
    unittest.main()
