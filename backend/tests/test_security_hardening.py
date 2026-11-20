"""Hardening checks for the LiveKit/recording release: secret length, playback-token
log redaction, stream rate limiting and self-hosted, integrity-pinned client script."""
import hashlib
import base64
import logging
import re
import unittest
from pathlib import Path

from pydantic import ValidationError

from app.config import Settings
from app.core.log_redaction import RedactTokensFilter, redact

ROOT = Path(__file__).resolve().parents[2]
BASE = dict(jwt_secret="x" * 40, database_url="sqlite://")


class LiveKitSecretLengthTests(unittest.TestCase):
    def build(self, **kw):
        return Settings(_env_file=None, **BASE, **kw)

    def test_blank_means_not_configured_and_is_allowed(self):
        self.assertEqual(self.build().livekit_api_secret, "")

    def test_a_short_secret_is_refused(self):
        for short in ("a", "short-secret", "s" * 31):
            with self.subTest(short):
                with self.assertRaises(ValidationError):
                    self.build(livekit_api_secret=short)

    def test_a_long_secret_is_accepted(self):
        self.assertEqual(self.build(livekit_api_secret="s" * 32).livekit_api_secret, "s" * 32)


class TokenRedactionTests(unittest.TestCase):
    TOKEN = "eyJhbGciOiJIUzI1NiJ9.payload.signature"

    def test_the_query_token_is_masked(self):
        out = redact(f"GET /api/recordings/abc/stream?t={self.TOKEN} HTTP/1.1")
        self.assertNotIn(self.TOKEN, out)
        self.assertIn("?t=[redacted]", out)

    def test_other_parameters_are_kept(self):
        out = redact(f"/x?a=1&t={self.TOKEN}&b=2")
        self.assertEqual(out, "/x?a=1&t=[redacted]&b=2")

    def test_the_access_log_filter_masks_uvicorn_style_args(self):
        record = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, '%s - "%s %s HTTP/%s" %d',
                                   ("1.2.3.4:5", "GET", f"/api/recordings/abc/stream?t={self.TOKEN}", "1.1", 206), None)
        RedactTokensFilter().filter(record)
        self.assertNotIn(self.TOKEN, record.getMessage())
        self.assertIn("206", record.getMessage())

    def test_a_t_inside_a_word_is_not_touched(self):
        self.assertEqual(redact("/x?part=1&sort=asc"), "/x?part=1&sort=asc")


class VendoredClientScriptTests(unittest.TestCase):
    FILE = ROOT / "arckenites-orange" / "vendor" / "livekit-client-2.5.7.umd.js"

    def test_pages_load_the_vendored_script_with_a_matching_integrity_hash(self):
        digest = "sha384-" + base64.b64encode(hashlib.sha384(self.FILE.read_bytes()).digest()).decode()
        for page in ("classroom.html", "meeting-room.html"):
            html = (ROOT / "arckenites-orange" / page).read_text(encoding="utf-8")
            with self.subTest(page):
                self.assertNotRegex(html, r"https?://[^\"']*livekit-client")
                tag = re.search(r'<script[^>]*vendor/livekit-client-2\.5\.7\.umd\.js[^>]*>', html)
                self.assertIsNotNone(tag)
                self.assertIn(f'integrity="{digest}"', tag.group(0))
                self.assertIn("crossorigin", tag.group(0))



class StreamRateLimitTests(unittest.TestCase):
    def test_the_stream_route_is_rate_limited_and_the_limit_is_not_trivially_low(self):
        from app.core.rate_limit import limiter
        import app.api.routes_recordings as routes
        limits = limiter._route_limits.get(f"{routes.stream_recording.__module__}.{routes.stream_recording.__name__}")
        self.assertTrue(limits, "stream_recording has no rate limit")
        # a player issues many Range requests per minute: the cap must leave room for normal playback
        self.assertGreaterEqual(limits[0].limit.amount, 120)


if __name__ == "__main__":
    unittest.main()
