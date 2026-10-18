"""Preparation-only reset policy against a disposable, FK-enforced SQLite DB.

No reset executor, migrations, filesystem cleanup, cloud calls or production
connection is used. Database writes are fixture creation only.
"""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine, event, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles

from app.database import Base
from app.models import (AdminRole, AdminProfile, User, RoleEnum, StudentProfile, StaffProfile,
                        Conversation, DirectMessage, SupportTicket, SupportMessage, SupportAttachment)
from app.models.support import SenderTypeEnum, TicketCategoryEnum
from app.models.mfa import MfaCredential
from maintenance.reset_policy import ALL_TABLES, preview
from maintenance.reset_prepare import main, source_inventory, validate_target, write_report


@compiles(JSONB, "sqlite")
def _jsonb_sqlite(type_, compiler, **kwargs):
    return "JSON"


class ResetPreparationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="arckenites-reset-preparation-tests-")
        self.addCleanup(self.temp.cleanup)
        self.engine = create_engine(f"sqlite:///{Path(self.temp.name) / 'isolated.sqlite'}")
        self.addCleanup(self.engine.dispose)
        @event.listens_for(self.engine, "connect")
        def enforce_fk(connection, record):
            connection.execute("PRAGMA foreign_keys=ON")
        Base.metadata.create_all(self.engine)
        with self.engine.begin() as conn:
            conn.execute(AdminRole.__table__.insert(), [
                {"id": 17, "name": "Super Admin", "slug": "super_admin"},
                {"id": 24, "name": "Other Admin", "slug": "ordinary"},
            ])
            conn.execute(User.__table__.insert(), [
                {"id": 101, "username": "fixture.super.one", "full_name": "Fixture", "role": RoleEnum.admin, "admin_role_id": 17, "password_hash": "fixture-hash-unchanged", "is_active": True},
                {"id": 102, "username": "fixture.super.two", "full_name": "Fixture", "role": RoleEnum.admin, "admin_role_id": 17, "password_hash": "fixture-hash-unchanged", "is_active": False},
                {"id": 103, "username": "fixture.admin", "full_name": "Fixture", "role": RoleEnum.admin, "admin_role_id": 24, "password_hash": "fixture", "is_active": True},
                {"id": 104, "username": "fixture.staff", "full_name": "Fixture", "role": RoleEnum.staff, "admin_role_id": None, "password_hash": "fixture", "is_active": True},
                {"id": 105, "username": "fixture.student", "full_name": "Fixture", "role": RoleEnum.student, "admin_role_id": None, "password_hash": "fixture", "is_active": True},
                {"id": 106, "username": "fixture.community", "full_name": "Fixture", "role": RoleEnum.community, "admin_role_id": None, "password_hash": "fixture", "is_active": True},
            ])
            conn.execute(AdminProfile.__table__.insert(), {"user_id": 101})
            conn.execute(AdminProfile.__table__.insert(), {"user_id": 103})
            conn.execute(StudentProfile.__table__.insert(), {"user_id": 105, "student_code": "FIXTURE-1"})
        self.metadata = source_inventory()

    def rows(self):
        result = {}
        with self.engine.connect() as conn:
            for table in Base.metadata.sorted_tables:
                primary_key = next(iter(table.primary_key.columns)).name
                names = {primary_key} | {f["source_column"] for f in self.metadata["foreign_keys"] if f["source_table"] == table.name}
                if table.name == "users":
                    names |= {"role", "is_active", "admin_role_id"}
                if table.name == "admin_roles":
                    names.add("slug")
                columns = [table.c[name] for name in sorted(names)]
                if primary_key != "id":
                    columns.append(table.c[primary_key].label("id"))
                result[table.name] = [dict(row) for row in conn.execute(select(*columns)).mappings()]
        return result

    def test_current_schema_and_fk_inventory_fully_classified(self):
        self.assertEqual(self.metadata["table_count"], 48)
        self.assertEqual(len(self.metadata["foreign_keys"]), 65)
        self.assertEqual(len(self.metadata["model_indexes"]), 60)
        # Includes uniqueness, primary keys and foreign keys for the MFA tables.
        self.assertEqual(len(self.metadata["model_constraints"]), 133)
        self.assertEqual(self.metadata["unclassified_model_tables"], [])
        self.assertFalse(self.metadata["production_schema_verified"])
        self.assertEqual(set(self.metadata["tables"]) | {"alembic_version"}, ALL_TABLES)

    def test_super_admin_accounts_protected_other_admins_candidate(self):
        plan = preview(self.rows(), self.metadata["foreign_keys"])
        self.assertEqual(plan["super_admin_ids"], [101, 102])
        self.assertEqual(plan["protected_admin_ids"], [101, 102])
        self.assertEqual(plan["admin_ids_requiring_individual_review"], [])
        self.assertEqual(plan["candidate_user_ids"], [103, 104, 105, 106])
        self.assertEqual(plan["proposed_actions"]["users"][103], "candidate_delete")
        self.assertEqual(plan["proposed_actions"]["users"][102], "retain")
        self.assertFalse(plan["execution_enabled"])

    def test_mfa_credentials_follow_account_retention_without_exposing_secrets(self):
        with self.engine.begin() as conn:
            conn.execute(MfaCredential.__table__.insert(), [
                {"user_id": 101, "secret_ciphertext": "protected-fixture-ciphertext"},
                {"user_id": 105, "secret_ciphertext": "candidate-fixture-ciphertext"},
            ])
        plan = preview(self.rows(), self.metadata["foreign_keys"])
        self.assertEqual(plan["proposed_actions"]["mfa_credentials"][101], "retain")
        self.assertEqual(plan["proposed_actions"]["mfa_credentials"][105], "candidate_delete")
        self.assertNotIn("fixture-ciphertext", json.dumps(plan))

    def test_mismatched_staff_profile_never_makes_protected_account_deletable(self):
        with self.engine.begin() as conn:
            conn.execute(StaffProfile.__table__.insert(), {"user_id": 101, "staff_code": "FIXTURE-STF"})
        plan = preview(self.rows(), self.metadata["foreign_keys"])
        self.assertNotIn(101, plan["candidate_user_ids"])
        self.assertEqual(plan["counts"]["staff_profiles"]["candidate_delete"], 0)

    def test_operational_account_with_admin_profile_is_manual(self):
        with self.engine.begin() as conn:
            conn.execute(AdminProfile.__table__.insert(), {"user_id": 104})
        plan = preview(self.rows(), self.metadata["foreign_keys"])
        self.assertNotIn(104, plan["candidate_user_ids"])
        self.assertTrue(any("user ID 104" in b for b in plan["blockers"]))

    def test_protected_contribution_keeps_whole_shared_thread(self):
        with self.engine.begin() as conn:
            conn.execute(Conversation.__table__.insert(), [
                {"id": 11, "user_a_id": 101, "user_b_id": 105},
                {"id": 12, "user_a_id": 104, "user_b_id": 105},
            ])
            conn.execute(DirectMessage.__table__.insert(), [
                {"conversation_id": 11, "sender_id": 105, "body": "fixture"},
                {"conversation_id": 12, "sender_id": 104, "body": "fixture"},
            ])
            conn.execute(SupportTicket.__table__.insert(), {"id": 31, "ticket_number": "FIXTURE", "requester_id": 105,
                                                          "subject": "fixture", "category": TicketCategoryEnum.other})
            conn.execute(SupportMessage.__table__.insert(), [
                {"id": 41, "ticket_id": 31, "sender_id": 105, "sender_type": SenderTypeEnum.student, "message": "fixture"},
                {"id": 42, "ticket_id": 31, "sender_id": 101, "sender_type": SenderTypeEnum.admin, "message": "fixture"},
            ])
            conn.execute(SupportAttachment.__table__.insert(), {"id": 51, "ticket_id": 31, "message_id": 41,
                "file_name": "fixture.pdf", "file_size": 1, "file_type": "application/pdf", "storage_reference": "fixture.pdf"})
        plan = preview(self.rows(), self.metadata["foreign_keys"])
        self.assertEqual(plan["proposed_actions"]["conversations"][11], "retain")
        self.assertEqual(plan["proposed_actions"]["conversations"][12], "candidate_delete")
        self.assertEqual(plan["proposed_actions"]["support_tickets"][31], "retain")
        self.assertEqual(plan["proposed_actions"]["support_messages"][41], "retain")
        self.assertEqual(plan["proposed_actions"]["support_attachments"][51], "retain")
        self.assertIn({"table": "support_tickets", "id": 31, "column": "requester_id"}, plan["proposed_detach_links"])

    def test_preserved_settings_attribution_is_a_blocker_not_silent_detachment(self):
        rows = self.rows()
        rows["system_settings"] = [{"id": 1, "updated_by_id": 104}]
        plan = preview(rows, self.metadata["foreign_keys"])
        self.assertTrue(any("system_settings.updated_by_id" in b for b in plan["blockers"]))
        self.assertFalse(any(d["table"] == "system_settings" for d in plan["proposed_detach_links"]))

    def test_audits_are_retained_nonnullable_actor_link_blocks_removal(self):
        rows = self.rows()
        rows["auth_audit_log"] = [{"id": 1, "user_id": 105}]
        rows["lab_access_audit_log"] = [{"id": 2, "student_id": 105, "admin_id": 104}]
        plan = preview(rows, self.metadata["foreign_keys"])
        self.assertEqual(plan["counts"]["auth_audit_log"]["candidate_delete"], 0)
        self.assertEqual(plan["counts"]["lab_access_audit_log"]["candidate_delete"], 0)
        self.assertTrue(any("lab_access_audit_log.admin_id" in b for b in plan["blockers"]))

    def test_unknown_table_and_missing_super_admin_block(self):
        rows = self.rows()
        rows["external_custom"] = [{"id": 1}]
        rows["users"] = [r for r in rows["users"] if r["id"] not in (101, 102)]
        plan = preview(rows, self.metadata["foreign_keys"])
        self.assertEqual(plan["counts"]["external_custom"]["manual"], 1)
        self.assertIn("No current Super Admin account was identified", plan["blockers"])

    def test_pending_cleanup_active_vm_live_media_have_explicit_holds(self):
        rows = self.rows()
        rows["file_cleanup_jobs"] = [{"id": 1}]
        rows["lab_vm_access"] = [{"id": 1, "student_id": 105, "vm_id": 1, "granted_by": 101, "status": "active"}]
        rows["meetings"] = [{"id": 1, "host_id": 101, "batch_id": None, "status": "LIVE"}]
        plan = preview(rows, self.metadata["foreign_keys"])
        self.assertTrue(any("cleanup queue" in b for b in plan["blockers"]))
        self.assertTrue(any("Active VM" in b for b in plan["blockers"]))
        self.assertTrue(any("Live meetings" in b for b in plan["blockers"]))

    def test_preview_changes_no_fixture_records_and_emits_no_secrets(self):
        statements = []
        def capture(conn, cursor, statement, params, context, many):
            statements.append(statement)
        event.listen(self.engine, "before_cursor_execute", capture)
        before = self.rows()
        plan = preview(before, self.metadata["foreign_keys"])
        self.assertEqual(self.rows(), before)
        self.assertTrue(all(s.lstrip().upper().startswith("SELECT") for s in statements))
        serialized = json.dumps(plan)
        for forbidden in ("password_hash", "fixture-hash-unchanged", "username", "token_hash", "storage_reference"):
            self.assertNotIn(forbidden, serialized)

    def test_target_guard_refuses_unverified_or_mismatched_production(self):
        actual = {"database": "fixture", "server_address": "192.0.2.1", "server_port": 5432,
                  "database_oid": 42, "database_user": "fixture_reader"}
        valid = {**actual, "environment": "production", "verified_by_operator": True}
        validate_target(actual, valid)
        for expected in ({}, {**valid, "environment": "staging"}, {**valid, "database_oid": 99},
                         {**valid, "database_oid": 0}, {**valid, "server_address": "<unknown>"}):
            with self.assertRaises(ValueError):
                validate_target(actual, expected)

    def test_output_is_exclusive_and_live_evidence_cannot_enter_git(self):
        dest = Path(self.temp.name) / "report.json"
        write_report(dest, {"fixture": True})
        with self.assertRaises(FileExistsError):
            write_report(dest, {})
        with self.assertRaises(ValueError):
            write_report(Path(__file__).parent / "must-not-exist.json", {}, live=True)

    def test_execute_always_refuses_before_connection(self):
        with patch("psycopg.connect") as connect, contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as result:
                main(["execute"])
            self.assertEqual(result.exception.code, 2)
            connect.assert_not_called()
