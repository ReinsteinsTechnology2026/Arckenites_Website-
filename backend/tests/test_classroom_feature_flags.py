"""LiveKit stays OFF in production until it is deliberately switched on, and existing Jitsi keeps working.

Three independent switches, all default off: LIVEKIT_CLASSROOM_ENABLED (batch classrooms + their routes),
LIVEKIT_MEETINGS_ENABLED (general meetings) and RECORDING_ENABLED (recording inside a classroom). Having LiveKit
configured, or recording enabled, must never expose the classroom or move meetings off Jitsi by itself.
"""
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI

from app.api.classroom_routes import include_classroom_routes
from app.config import Settings, settings
from tests.test_job_queue_and_class_lifecycle import Harness

BACKEND = Path(__file__).resolve().parents[1]
CONFIGURED = dict(livekit_url="wss://media.example.test", livekit_api_key="APItestkey",
                  livekit_api_secret="test-livekit-secret-" + "x" * 32)


def make_settings(**kw) -> Settings:
    base = dict(database_url="sqlite:///:memory:", jwt_secret="x" * 40)
    base.update(kw)
    return Settings(_env_file=None, **base)


class SettingsFlagTests(unittest.TestCase):
    def test_every_switch_defaults_to_off(self):
        s = make_settings()
        self.assertEqual((s.livekit_classroom_enabled, s.livekit_meetings_enabled, s.recording_enabled), (False, False, False))

    def test_env_is_normalised_for_case_and_whitespace(self):
        for raw in ("production", "Production", "PRODUCTION", "  production  ", "\tProduction\n"):
            self.assertTrue(make_settings(env=raw).is_production, repr(raw))
        for raw in ("development", "test", "staging", "", "prod", "production-like"):
            self.assertFalse(make_settings(env=raw).is_production, repr(raw))

    def test_production_needs_the_classroom_flag_to_have_routes(self):
        self.assertFalse(make_settings(env="production").classroom_routes_enabled)
        self.assertFalse(make_settings(env=" Production ").classroom_routes_enabled)
        self.assertTrue(make_settings(env="production", livekit_classroom_enabled=True).classroom_routes_enabled)
        self.assertTrue(make_settings(env="development").classroom_routes_enabled)
        self.assertTrue(make_settings(env="test").classroom_routes_enabled)

    def test_recording_alone_never_exposes_the_classroom_in_production(self):
        s = make_settings(env="production", recording_enabled=True, **CONFIGURED)
        self.assertFalse(s.classroom_routes_enabled)
        self.assertFalse(s.classroom_available)

    def test_classroom_available_needs_the_flag_and_a_configured_media_server(self):
        self.assertFalse(make_settings(env="development", **CONFIGURED).classroom_available)                       # flag off
        self.assertFalse(make_settings(env="development", livekit_classroom_enabled=True).classroom_available)     # not configured
        for missing in ("livekit_url", "livekit_api_key", "livekit_api_secret"):
            partial = {**CONFIGURED, missing: ""}
            self.assertFalse(make_settings(livekit_classroom_enabled=True, **partial).classroom_available, missing)
        self.assertTrue(make_settings(env="development", livekit_classroom_enabled=True, **CONFIGURED).classroom_available)
        self.assertTrue(make_settings(env="production", livekit_classroom_enabled=True, **CONFIGURED).classroom_available)

    def test_meetings_move_to_livekit_only_with_their_own_flag(self):
        self.assertFalse(make_settings(**CONFIGURED).meetings_on_livekit)
        self.assertFalse(make_settings(livekit_classroom_enabled=True, recording_enabled=True, **CONFIGURED).meetings_on_livekit)
        self.assertFalse(make_settings(livekit_meetings_enabled=True).meetings_on_livekit)    # not configured
        self.assertTrue(make_settings(livekit_meetings_enabled=True, **CONFIGURED).meetings_on_livekit)


class RouteRegistrationTests(unittest.TestCase):
    CLASS_PATHS = ("/api/class-runs", "/api/recordings", "/api/media/webhooks/livekit")

    def paths_for(self, **kw):
        app = FastAPI()
        with patch.multiple(settings, **kw):
            registered = include_classroom_routes(app)
        return registered, {r.path for r in app.routes}

    def test_availability_is_always_registered_but_class_routes_follow_the_flag(self):
        registered, paths = self.paths_for(env="production", livekit_classroom_enabled=False)
        self.assertFalse(registered)
        self.assertIn("/api/classroom/availability", paths)
        for prefix in self.CLASS_PATHS:
            self.assertFalse(any(p.startswith(prefix) for p in paths), prefix)

    def test_production_with_the_flag_registers_every_class_route(self):
        registered, paths = self.paths_for(env=" PRODUCTION ", livekit_classroom_enabled=True)
        self.assertTrue(registered)
        for prefix in self.CLASS_PATHS:
            self.assertTrue(any(p.startswith(prefix) for p in paths), prefix)

    def test_recording_flag_alone_does_not_register_class_routes_in_production(self):
        registered, paths = self.paths_for(env="Production", livekit_classroom_enabled=False, recording_enabled=True)
        self.assertFalse(registered)
        self.assertFalse(any(p.startswith("/api/class-runs") for p in paths))

    def test_non_production_keeps_the_routes_for_development_and_tests(self):
        registered, paths = self.paths_for(env="development", livekit_classroom_enabled=False)
        self.assertTrue(registered)
        self.assertTrue(any(p.startswith("/api/class-runs") for p in paths))


class RealAppProductionRegistrationTests(unittest.TestCase):
    """Imports the real application in a fresh process, as the server does, with production-style environments."""

    def routes_in_fresh_process(self, **env):
        code = ("import json; from app.main import app; p = sorted({r.path for r in app.routes});"
                "print(json.dumps(p))")
        full = {**os.environ, "DATABASE_URL": "sqlite:///:memory:", "JWT_SECRET": "t" * 40, "PYTHONUTF8": "1"}
        for k in ("ENV", "RECORDING_ENABLED", "LIVEKIT_CLASSROOM_ENABLED", "LIVEKIT_MEETINGS_ENABLED", "LIVEKIT_URL",
                  "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET", "ARCK_ENABLE_POSTGRES_INTEGRATION"):
            full.pop(k, None)
        full.update(env)
        out = subprocess.run([sys.executable, "-c", code], cwd=str(BACKEND), env=full, capture_output=True, text=True, timeout=180)
        self.assertEqual(out.returncode, 0, out.stderr[-600:])
        return json.loads(out.stdout.strip().splitlines()[-1])

    LK = {"LIVEKIT_URL": "wss://media.example.test", "LIVEKIT_API_KEY": "APItestkey", "LIVEKIT_API_SECRET": "s" * 40}

    def classroom_present(self, paths):
        return any(p.startswith("/api/class-runs") for p in paths)

    def test_production_default_has_no_class_routes_but_keeps_jitsi_meetings_and_the_availability_check(self):
        paths = self.routes_in_fresh_process(ENV="production")
        self.assertFalse(self.classroom_present(paths))
        self.assertFalse(any(p.startswith(("/api/recordings", "/api/media/webhooks")) for p in paths))
        self.assertIn("/api/classroom/availability", paths)
        self.assertTrue(any(p.startswith("/api/meetings") for p in paths))      # existing Jitsi meetings untouched

    def test_production_spelled_with_case_or_spaces_is_still_production(self):
        for raw in ("Production", " production ", "PRODUCTION"):
            self.assertFalse(self.classroom_present(self.routes_in_fresh_process(ENV=raw)), repr(raw))

    def test_production_with_recording_and_livekit_configured_but_no_classroom_flag_stays_dark(self):
        paths = self.routes_in_fresh_process(ENV="production", RECORDING_ENABLED="true", **self.LK)
        self.assertFalse(self.classroom_present(paths))

    def test_production_with_the_classroom_flag_registers_the_routes(self):
        paths = self.routes_in_fresh_process(ENV="production", LIVEKIT_CLASSROOM_ENABLED="true", **self.LK)
        self.assertTrue(self.classroom_present(paths))
        self.assertIn("/api/classroom/availability", paths)

    def test_development_default_registers_the_routes(self):
        self.assertTrue(self.classroom_present(self.routes_in_fresh_process(ENV="development")))


class AvailabilityEndpointTests(Harness):
    def get(self, who=None):
        return self.client.get("/api/classroom/availability", headers=self.headers(who) if who else {})

    def test_requires_login(self):
        self.assertEqual(self.get().status_code, 401)

    def test_is_false_by_default_even_though_livekit_is_configured_and_recording_is_on(self):
        for who in ("trainer", "student", "outsider", "other-trainer"):
            r = self.get(who)
            self.assertEqual((r.status_code, r.json()), (200, {"classroom_enabled": False}), who)

    def test_is_true_only_with_the_flag_and_a_configured_server(self):
        with patch.object(settings, "livekit_classroom_enabled", True):
            self.assertEqual(self.get("student").json(), {"classroom_enabled": True})
            with patch.object(settings, "livekit_api_secret", ""):
                self.assertEqual(self.get("student").json(), {"classroom_enabled": False})

    def test_answer_is_the_same_boolean_for_every_role_and_leaks_nothing_else(self):
        with patch.object(settings, "livekit_classroom_enabled", True):
            bodies = {who: self.get(who).json() for who in ("trainer", "student", "outsider")}
        self.assertEqual(len({json.dumps(b) for b in bodies.values()}), 1)
        self.assertEqual(set(next(iter(bodies.values()))), {"classroom_enabled"})
        self.assertNotIn("media.example.test", json.dumps(bodies))


class GeneralMeetingsStayOnJitsiTests(unittest.TestCase):
    def fields(self, **kw):
        from app.core.meetings import livekit_join_fields
        from types import SimpleNamespace
        meeting, user = SimpleNamespace(id=5), SimpleNamespace(id=7, full_name="Pat")
        with patch.multiple(settings, **kw):
            return livekit_join_fields(meeting, user, is_moderator=False)

    def test_configuring_livekit_and_enabling_the_classroom_does_not_move_meetings(self):
        self.assertEqual(self.fields(**CONFIGURED, livekit_classroom_enabled=True, recording_enabled=True), {})

    def test_meetings_use_livekit_only_with_their_own_flag(self):
        got = self.fields(**CONFIGURED, livekit_meetings_enabled=True)
        self.assertEqual(set(got), {"livekit_url", "livekit_token"})

    def test_unconfigured_server_never_hands_out_a_token_even_with_the_flag(self):
        self.assertEqual(self.fields(livekit_meetings_enabled=True, livekit_url="", livekit_api_key="", livekit_api_secret=""), {})


if __name__ == "__main__":
    unittest.main()
