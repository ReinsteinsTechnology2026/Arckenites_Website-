"""Phase 2 milestone 3: a student's private class notes.

Isolated: temp SQLite, no network, no real data. Reuses the Phase 1 test world
(one class with its trainer and students, an unrelated class, admins, a general
meeting).
"""
import importlib.util
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import create_engine, inspect, select

from app.core import student_deletion
from app.database import Base
from app.models import BatchEnrollment, ClassPrivateNote
from app.schemas.class_notes import MAX_NOTE_CHARS
from test_recording_permissions import BACKEND, Base_, QaSession

ALL_OTHERS = ["trainer", "other-trainer", "outsider", "guest", "host", "viewer-admin", "super-admin", "plain-admin"]


class PrivateNotesBase(Base_):
    def url(self, run_id=None):
        return f"/api/class-runs/{run_id or self.run_id}/notes/me"

    def get(self, who, run_id=None):
        return self.client.get(self.url(run_id), headers=self.headers(who))

    def put(self, who, content, run_id=None, **extra):
        return self.client.post(self.url(run_id), headers=self.headers(who), json={"content": content, **extra})


class NotesRoundTripTests(PrivateNotesBase):
    def test_a_new_note_is_empty_then_saves_and_reloads(self):
        first = self.get("student")
        self.assertEqual((first.status_code, first.json()["content"]), (200, ""))
        self.assertEqual(self.put("student", "TCP is connection oriented").status_code, 200)
        again = self.get("student").json()
        self.assertEqual(again["content"], "TCP is connection oriented")
        self.assertEqual(again["run_id"], self.run_id)

    def test_autosave_overwrites_one_row_instead_of_piling_up(self):
        for text in ("a", "ab", "abc"):
            self.assertEqual(self.put("student", text).status_code, 200)
        self.assertEqual(self.get("student").json()["content"], "abc")
        self.assertEqual(self.count(ClassPrivateNote), 1)

    def test_the_size_limit_is_enforced(self):
        self.assertEqual(self.put("student", "x" * MAX_NOTE_CHARS).status_code, 200)
        self.assertEqual(self.put("student", "x" * (MAX_NOTE_CHARS + 1)).status_code, 422)
        self.assertEqual(len(self.get("student").json()["content"]), MAX_NOTE_CHARS)


class NotesPrivacyTests(PrivateNotesBase):
    def test_two_students_never_see_each_others_notes(self):
        self.put("student", "student-secret")
        self.put("removed", "other-secret")          # a second, still-enrolled student
        self.assertEqual(self.get("student").json()["content"], "student-secret")
        self.assertEqual(self.get("removed").json()["content"], "other-secret")
        self.assertEqual(self.count(ClassPrivateNote), 2)

    def test_a_second_student_starts_empty_and_cannot_read_the_first(self):
        self.put("student", "student-secret")
        body = self.get("removed")
        self.assertEqual(body.json()["content"], "")
        self.assertNotIn("student-secret", body.text)

    def test_a_user_id_in_the_body_cannot_redirect_a_write_to_someone_else(self):
        r = self.client.post(self.url(), headers=self.headers("removed"),
                            json={"content": "mine", "user_id": self.ids["student"], "run_id": 999})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.get("student").json()["content"], "")
        self.assertEqual(self.get("removed").json()["content"], "mine")
        with QaSession(self.engine) as db:
            row = db.scalar(select(ClassPrivateNote))
            self.assertEqual((row.user_id, row.run_id), (self.ids["removed"], self.run_id))

    def test_nobody_else_can_read_or_write_a_students_notes(self):
        self.put("student", "student-secret")
        for who in ALL_OTHERS:
            with self.subTest(who):
                self.assertIn(self.get(who).status_code, (403, 404))
                self.assertIn(self.put(who, "overwrite").status_code, (403, 404))
        self.assertEqual(self.get("student").json()["content"], "student-secret")

    def test_the_batch_trainer_has_no_read_path_to_student_notes(self):
        self.put("student", "student-secret")
        r = self.get("trainer")
        self.assertEqual(r.status_code, 403)
        self.assertNotIn("student-secret", r.text)

    def test_admins_and_outsiders_get_not_found_not_confirmation(self):
        for who in ("viewer-admin", "super-admin", "plain-admin", "outsider", "other-trainer"):
            with self.subTest(who):
                self.assertEqual(self.get(who).status_code, 404)

    def test_anonymous_requests_are_refused(self):
        self.assertIn(self.client.get(self.url()).status_code, (401, 403))
        self.assertIn(self.client.post(self.url(), json={"content": "x"}).status_code, (401, 403))

    def test_a_student_removed_from_the_batch_loses_access_but_keeps_the_row(self):
        self.put("removed", "kept")
        with QaSession(self.engine) as db:
            db.execute(BatchEnrollment.__table__.delete().where(BatchEnrollment.student_id == self.ids["removed"]))
            db.commit()
        self.assertEqual(self.get("removed").status_code, 404)
        self.assertEqual(self.put("removed", "again").status_code, 404)
        self.assertEqual(self.count(ClassPrivateNote), 1)

    def test_unknown_class_is_not_found(self):
        self.assertEqual(self.get("student", run_id=987654).status_code, 404)

    def test_notes_do_not_leak_through_the_recording_status_endpoint(self):
        self.put("student", "student-secret")
        for who in ("trainer", "student", "viewer-admin"):
            self.assertNotIn("student-secret", self.status(who).text)


class NotesLifecycleTests(PrivateNotesBase):
    def test_account_deletion_treats_the_notes_table_as_owned_by_the_student(self):
        self.assertEqual(student_deletion.OWNED.get("class_private_notes"), "user_id")
        table = Base.metadata.tables["class_private_notes"]
        for column in table.c:
            if any(fk.target_fullname == "users.id" for fk in column.foreign_keys):
                self.assertEqual(column.name, student_deletion.OWNED[table.name])


class NotesMigrationTests(unittest.TestCase):
    def load(self):
        path = BACKEND / "alembic" / "versions" / "c4e8a1d7f3b6_add_class_private_notes.py"
        spec = importlib.util.spec_from_file_location("c4e8a1d7f3b6", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_it_extends_the_single_head_and_is_additive(self):
        from alembic.config import Config
        from alembic.script import ScriptDirectory
        module = self.load()
        self.assertEqual(module.down_revision, "b8d3f6a2c915")
        cfg = Config(str(BACKEND / "alembic.ini"))
        cfg.set_main_option("script_location", str(BACKEND / "alembic"))
        script = ScriptDirectory.from_config(cfg)
        # Still exactly one head (later revisions, e.g. the production merge, sit above this one) and
        # this migration is still part of that head's history, unchanged and unrewritten.
        self.assertEqual(len(script.get_heads()), 1)
        self.assertIn("c4e8a1d7f3b6", {r.revision for r in script.walk_revisions()})
        source = Path(module.__file__).read_text(encoding="utf-8")
        self.assertNotRegex(source.split("def downgrade")[0].split("def upgrade")[1], r"drop_|DELETE|UPDATE|alter_")

    def test_upgrade_and_downgrade_on_a_throwaway_database_match_the_model(self):
        from alembic.migration import MigrationContext
        from alembic.operations import Operations
        module = self.load()
        with tempfile.TemporaryDirectory(prefix="arckenites-notes-mig-", ignore_cleanup_errors=True) as tmp:
            engine = create_engine(f"sqlite:///{Path(tmp) / 'm.sqlite'}")
            tables = [t for t in Base.metadata.sorted_tables if t.name != "class_private_notes"]
            Base.metadata.create_all(engine, tables=tables)
            with engine.begin() as conn:
                with Operations.context(MigrationContext.configure(conn)):
                    module.upgrade()
                insp = inspect(conn)
                cols = {c["name"] for c in insp.get_columns("class_private_notes")}
                self.assertEqual(cols, {c.name for c in ClassPrivateNote.__table__.c})
                uniques = {tuple(u["column_names"]) for u in insp.get_unique_constraints("class_private_notes")}
                self.assertIn(("run_id", "user_id"), uniques)
                with Operations.context(MigrationContext.configure(conn)):
                    module.downgrade()
                self.assertNotIn("class_private_notes", inspect(conn).get_table_names())
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
