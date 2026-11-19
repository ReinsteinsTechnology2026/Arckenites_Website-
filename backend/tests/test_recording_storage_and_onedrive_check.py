"""Storage abstraction (local staging) and the read-only OneDrive access check."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.config import settings
from app.core import recording_storage as rs
from app.core.onedrive_store import OneDriveStore
from maintenance import check_onedrive_access as check


class StagingStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="arck-staging-", ignore_cleanup_errors=True)
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)
        patcher = patch.object(settings, "recording_min_free_gb", 0.0)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_keys_are_server_generated_unique_and_safe(self):
        a, b = rs.new_staging_key(), rs.new_staging_key()
        self.assertNotEqual(a, b)
        for key in (a, b):
            self.assertRegex(key, r"^incoming/[0-9a-f]{32}\.mp4$")

    def test_path_traversal_and_absolute_keys_are_refused(self):
        store = rs.StagingStore(self.root)
        for bad in ("../outside.mp4", "incoming/../../outside.mp4", "/etc/passwd", "\\windows\\x", "", "a/../../b"):
            with self.subTest(bad), self.assertRaises(rs.StorageError):
                store.path_for(bad)
        self.assertEqual(store.path_for("incoming/x.mp4"), (self.root.resolve() / "incoming" / "x.mp4"))

    def test_existence_check_requires_a_nonempty_file(self):
        store = rs.StagingStore(self.root)
        (self.root / "incoming").mkdir()
        self.assertFalse(store.exists_nonempty("incoming/none.mp4"))
        (self.root / "incoming" / "empty.mp4").write_bytes(b"")
        self.assertFalse(store.exists_nonempty("incoming/empty.mp4"))
        (self.root / "incoming" / "ok.mp4").write_bytes(b"\x00\x01")
        self.assertTrue(store.exists_nonempty("incoming/ok.mp4"))
        self.assertFalse(store.exists_nonempty("../ok.mp4"))

    def test_sha256_streams_the_file(self):
        path = self.root / "f.bin"
        path.write_bytes(b"abc")
        self.assertEqual(rs.StagingStore.sha256_of(path),
                         "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")

    def test_readiness_reports_the_specific_problem(self):
        self.assertEqual(rs.StagingStore(self.root).readiness(), (True, None))
        self.assertIn("isn't available", rs.StagingStore(self.root / "missing").readiness()[1])
        self.assertIn("misconfigured", rs.StagingStore(rs.REPO_ROOT).readiness()[1])
        with patch.object(settings, "recording_min_free_gb", 10 ** 9):
            self.assertIn("nearly full", rs.StagingStore(self.root).readiness()[1])
        self.assertIn("isn't configured", rs.StagingStore("").readiness()[1])

    def test_staging_is_never_called_onedrive(self):
        self.assertEqual(rs.StagingStore.provider, "LOCAL_STAGING")
        self.assertNotEqual(rs.StagingStore.provider, OneDriveStore.provider)

    def test_onedrive_is_not_ready_until_enabled_and_a_destination_is_configured(self):
        store = OneDriveStore()
        ready, reason = store.readiness()
        self.assertFalse(ready)
        self.assertIn("disabled", reason)
        with patch.object(settings, "onedrive_upload_enabled", True), patch.object(settings, "onedrive_drive_id", ""):
            ready, reason = OneDriveStore().readiness()
            self.assertFalse(ready)  # a flag alone never makes it ready
            self.assertIn("destination", reason)


class OneDriveAccessCheckTests(unittest.TestCase):
    def test_mail_send_alone_grants_no_file_access(self):
        verdict = check.classify_roles(["Mail.Send"])
        self.assertTrue(verdict["can_send_mail"])
        self.assertFalse(verdict["can_upload_files"])
        self.assertEqual(verdict["file_write_roles"], [])

    def test_file_write_roles_are_recognised(self):
        self.assertTrue(check.classify_roles(["Mail.Send", "Files.ReadWrite.All"])["can_upload_files"])
        self.assertTrue(check.classify_roles(["Sites.ReadWrite.All"])["can_upload_files"])

    def test_read_only_and_scoped_roles_are_not_treated_as_write_access(self):
        self.assertFalse(check.classify_roles(["Files.Read.All"])["can_upload_files"])
        scoped = check.classify_roles(["Sites.Selected"])
        self.assertFalse(scoped["can_upload_files"])
        self.assertTrue(scoped["scoped_file_role"])

    def test_unconfigured_graph_exits_without_any_network_call(self):
        with patch.object(check.settings, "ms_graph_client_secret", ""), \
                patch("maintenance.check_onedrive_access.ms_graph_email._acquire_token") as acquire:
            self.assertEqual(check.main([]), 2)
            acquire.assert_not_called()

    def test_output_never_contains_the_token_or_secret(self):
        import io
        import contextlib
        import jwt
        token = jwt.encode({"roles": ["Mail.Send"]}, "k", algorithm="HS256")
        with patch.object(check.settings, "ms_graph_tenant_id", "tenant-xyz"), \
                patch.object(check.settings, "ms_graph_client_id", "client-xyz"), \
                patch.object(check.settings, "ms_graph_client_secret", "SECRET-VALUE-123"), \
                patch.object(check.settings, "ms_graph_sender_email", "sender@example.test"), \
                patch("maintenance.check_onedrive_access.ms_graph_email._acquire_token", return_value=token):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = check.main([])
        text = out.getvalue()
        self.assertEqual(code, 0)
        self.assertNotIn("SECRET-VALUE-123", text)
        self.assertNotIn(token, text)
        self.assertIn("CANNOT upload files", text)


if __name__ == "__main__":
    unittest.main()
