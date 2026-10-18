import os
import unittest
from unittest.mock import patch

from postgres_safety import isolated_postgres_enabled, qa_target_from_environment


class TestPostgresSafety(unittest.TestCase):
    base = {
        "ARCK_ENABLE_POSTGRES_INTEGRATION": "1",
        "ENV": "test",
        "ARCK_QA_DATABASE_MARKER": "isolated-unit-marker-2026",
        "DATABASE_URL": "postgresql+psycopg://u:p@127.0.0.1:55432/arckenites_qa_unit",
    }

    def test_rejected_urls_and_missing_opt_in_never_connect(self):
        for replacement in (
            {"ARCK_ENABLE_POSTGRES_INTEGRATION": "0"},
            {"ENV": "production"},
            {"DATABASE_URL": "postgresql+psycopg://u:p@db.example:5432/arckenites_qa_unit"},
            {"DATABASE_URL": "postgresql+psycopg://u:p@127.0.0.1:55432/production"},
            {"DATABASE_URL": "postgresql+psycopg://u:p@127.0.0.1:55432/arckenites_qa_unit?host=db.example"},
        ):
            with self.subTest(replacement=replacement), patch.dict(os.environ, self.base | replacement, clear=True), \
                 patch("postgres_safety.create_engine") as connect:
                self.assertIsNone(qa_target_from_environment())
                self.assertFalse(isolated_postgres_enabled())
                connect.assert_not_called()

    def test_missing_expected_marker_fails_before_connection(self):
        values = dict(self.base)
        values.pop("ARCK_QA_DATABASE_MARKER")
        with patch.dict(os.environ, values, clear=True), patch("postgres_safety.create_engine") as connect:
            self.assertFalse(isolated_postgres_enabled())
            connect.assert_not_called()

    def test_unreachable_database_fails_closed(self):
        values = self.base | {"DATABASE_URL": "postgresql+psycopg://u:p@127.0.0.1:1/arckenites_qa_unit"}
        with patch.dict(os.environ, values, clear=True):
            self.assertFalse(isolated_postgres_enabled())

    def test_configured_disposable_database_verifies_marker(self):
        if os.getenv("ARCK_ENABLE_POSTGRES_INTEGRATION") == "1":
            self.assertTrue(isolated_postgres_enabled())
        else:
            self.assertFalse(isolated_postgres_enabled())
