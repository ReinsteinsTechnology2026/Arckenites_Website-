"""REAL PostgreSQL integration tests for DELETE /admin/students/{id} and the
transaction-boundary fix in write_audit_event's new `commit` parameter.

These are NOT mocked — they run the actual app.api.routes_admin_students
.delete_student function against a real SQLAlchemy Session connected to
whatever database backend/.env's DATABASE_URL points at (the project's own
local dev Postgres, via backend/docker-compose.yml — never production).

Unlike test_student_deletion.py (MagicMock Session), these can actually
prove commit/rollback semantics: a mocked Session's commit()/rollback() are
no-ops that don't touch any data, so they cannot catch a bug where a
nested, independent commit (write_audit_event's own db.commit()) silently
persists a change before the caller's own rollback-on-failure logic ever
runs — which is exactly the real bug this file was written to catch and
now regression-tests the fix for.

SKIPPED ENTIRELY (not failed) if no database is reachable at
DATABASE_URL, so the normal `unittest discover` run still passes cleanly
in any environment without a running local Postgres.
"""

import unittest
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

from fastapi import HTTPException
from sqlalchemy import delete as sa_delete
from sqlalchemy import select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.api.routes_admin_students import _student_has_related_records, delete_student, permanently_delete_student, update_student
from app.api.routes_auth import login
from app.core.security import hash_password
from app.crud.user import generate_student_code, generate_username
from app.database import engine
from app.models.audit_log import AuthAuditLog, AuthEventType
from app.models.batch import Batch, BatchEnrollment, BatchStatusEnum, BatchTypeEnum
from app.models.session import AuthSession
from app.models.chat import Conversation, DirectMessage
from app.models.student import ProgramEnum, StudentProfile
from app.models.support import SupportMessage, SupportTicket, SenderTypeEnum, TicketCategoryEnum, TicketPriorityEnum, TicketStatusEnum
from app.models.user import RoleEnum, User
from app.schemas.admin_students import PermanentDeleteStudentRequest, UpdateStudentRequest
from postgres_safety import isolated_postgres_enabled

USERNAME_PREFIX = "integtest."


def _db_reachable() -> bool:
    if not isolated_postgres_enabled():
        return False
    try:
        with engine.connect() as conn:
            conn.execute(select(1))
        return True
    except OperationalError:
        return False


def make_request():
    from starlette.requests import Request as StarletteRequest

    scope = {
        "type": "http", "method": "DELETE", "path": "/admin/students/x",
        "headers": [(b"user-agent", b"integration-test")], "client": ("127.0.0.1", 0), "query_string": b"",
    }
    return StarletteRequest(scope)


@unittest.skipUnless(_db_reachable(), "No local PostgreSQL reachable at DATABASE_URL — skipping real-DB tests")
class TestStudentDeletionIntegration(unittest.TestCase):
    """Each test gets its own Session and cleans up only the disposable rows
    it creates (prefixed `integtest.` on the username), never touching any
    pre-existing row. setUp/tearDown never resets or drops anything."""

    def setUp(self):
        self.db = Session(bind=engine)
        self.actor = self.db.scalar(select(User).where(User.role == RoleEnum.admin).limit(1))
        if self.actor is None:
            self.skipTest("No admin user exists in this local DB to act as the test's actor")
        self._created_user_ids: list[int] = []

    def tearDown(self):
        self.db.rollback()  # discard anything left pending from an assertion failure mid-test
        for uid in self._created_user_ids:
            user = self.db.get(User, uid)
            if user is None:
                continue
            self.db.execute(update(AuthAuditLog).where(AuthAuditLog.user_id == uid).values(user_id=None))
            self.db.execute(sa_delete(SupportTicket).where(SupportTicket.requester_id == uid))
            self.db.execute(sa_delete(BatchEnrollment).where(BatchEnrollment.student_id == uid))
            self.db.execute(sa_delete(AuthSession).where(AuthSession.user_id == uid))
            self.db.execute(sa_delete(StudentProfile).where(StudentProfile.user_id == uid))
            self.db.delete(user)
        self.db.execute(sa_delete(Batch).where(Batch.name == "integtest-batch"))
        # Audit rows naming a deleted student as `target`/`username_attempted`
        # (the normal case — the acting admin, not the student, is user_id)
        # aren't touched by the user_id-keyed cleanup above, and would
        # otherwise accumulate across repeated test runs since every run
        # reuses the same deterministic username per suffix. Purge by the
        # fixed test-username prefix instead — never a real admin/student's
        # audit history, since nothing outside this file uses that prefix.
        # generate_username() strips non-alphanumeric characters, so the
        # literal "." in USERNAME_PREFIX never survives into a real
        # username — match on "integtest" without it instead.
        self.db.execute(sa_delete(AuthAuditLog).where(AuthAuditLog.target.ilike("%integtest%")))
        self.db.execute(sa_delete(AuthAuditLog).where(AuthAuditLog.username_attempted.ilike("%integtest%")))
        self.db.commit()
        self.db.close()

    def _make_student(self, suffix: str) -> User:
        full_name = f"Integration Test {suffix}"
        username = generate_username(self.db, f"{USERNAME_PREFIX}{suffix}", RoleEnum.student)
        user = User(
            username=username, password_hash=hash_password("Integration#Test1"), full_name=full_name,
            role=RoleEnum.student, is_active=True, must_change_password=True,
        )
        self.db.add(user)
        self.db.flush()
        self.db.add(StudentProfile(
            user_id=user.id, student_code=generate_student_code(self.db),
            program=ProgramEnum.official_certification,
        ))
        self.db.commit()
        self.db.refresh(user)
        self._created_user_ids.append(user.id)
        return user

    # ---------- existing-behavior regression: clean hard delete unchanged ----------
    def test_clean_student_is_hard_deleted(self):
        student = self._make_student("clean")
        result = delete_student(student.id, make_request(), db=self.db, actor=self.actor)
        self.assertEqual(result.action, "deleted")
        self.assertIsNone(self.db.get(User, student.id))
        self._created_user_ids.remove(student.id)  # already gone — nothing to clean up

    # ---------- deactivation path: student status + session revocation + audit, atomically ----------
    def test_permanent_delete_detaches_shared_history_on_postgresql(self):
        student = self._make_student("permanentshared")
        uid = student.id
        ticket = SupportTicket(
            requester_id=uid, ticket_number=f"TKT-QA-{uid}", subject="Shared QA history",
            status=TicketStatusEnum.open, priority=TicketPriorityEnum.medium, category=TicketCategoryEnum.other,
        )
        conversation = Conversation(user_a_id=min(self.actor.id, uid), user_b_id=max(self.actor.id, uid))
        self.db.add_all([ticket, conversation])
        self.db.flush()
        self.db.add_all([
            SupportMessage(ticket_id=ticket.id, sender_id=uid, sender_type=SenderTypeEnum.student,
                           message="Student contribution"),
            SupportMessage(ticket_id=ticket.id, sender_id=self.actor.id, sender_type=SenderTypeEnum.admin,
                           message="Admin contribution"),
            DirectMessage(conversation_id=conversation.id, sender_id=uid, body="Student chat"),
            DirectMessage(conversation_id=conversation.id, sender_id=self.actor.id, body="Admin chat"),
            AuthSession(id=uuid.uuid4(), user_id=uid,
                        expires_at=datetime.now(timezone.utc).replace(year=2099),
                        ip_address="127.0.0.1", user_agent="qa"),
        ])
        self.db.commit()
        ticket_id, conversation_id = ticket.id, conversation.id
        try:
            result = permanently_delete_student(
                uid, PermanentDeleteStudentRequest(confirm_username=student.username,
                                                    confirmation="PERMANENTLY DELETE"),
                make_request(), db=self.db, actor=self.actor,
            )
            self.assertEqual(result.action, "permanently_deleted")
            self.assertIsNone(self.db.get(User, uid))
            self._created_user_ids.remove(uid)
            self.db.expire_all()
            self.assertIsNone(self.db.get(SupportTicket, ticket_id).requester_id)
            messages = self.db.scalars(select(SupportMessage).where(SupportMessage.ticket_id == ticket_id)).all()
            self.assertEqual(len(messages), 2)
            self.assertEqual({m.sender_id for m in messages}, {None, self.actor.id})
            self.assertIsNotNone(self.db.get(Conversation, conversation_id))
            chats = self.db.scalars(select(DirectMessage).where(DirectMessage.conversation_id == conversation_id)).all()
            self.assertEqual(len(chats), 2)
            self.assertEqual({m.sender_id for m in chats}, {None, self.actor.id})
            self.assertIsNone(self.db.scalar(select(AuthSession).where(AuthSession.user_id == uid)))
            audit = self.db.scalar(select(AuthAuditLog).where(
                AuthAuditLog.event_type == AuthEventType.student_deleted,
                AuthAuditLog.target == f"student:{uid}"))
            self.assertIsNotNone(audit)
            self.assertEqual(audit.user_id, self.actor.id)
        finally:
            self.db.rollback()
            self.db.execute(sa_delete(DirectMessage).where(DirectMessage.conversation_id == conversation_id))
            self.db.execute(sa_delete(Conversation).where(Conversation.id == conversation_id))
            self.db.execute(sa_delete(SupportMessage).where(SupportMessage.ticket_id == ticket_id))
            self.db.execute(sa_delete(SupportTicket).where(SupportTicket.id == ticket_id))
            self.db.execute(sa_delete(AuthAuditLog).where(AuthAuditLog.target == f"student:{uid}"))
            self.db.commit()

    def test_deactivation_commits_status_sessions_and_audit_together(self):
        student = self._make_student("withticket")
        self.db.add(SupportTicket(
            requester_id=student.id, ticket_number=f"TKT-{student.id}", subject="integration check",
            status=TicketStatusEnum.open, priority=TicketPriorityEnum.medium, category=TicketCategoryEnum.other,
        ))
        session_id = uuid.uuid4()
        self.db.add(AuthSession(
            id=session_id, user_id=student.id, expires_at=datetime.now(timezone.utc).replace(year=2099),
            ip_address="127.0.0.1", user_agent="integration-test",
        ))
        self.db.commit()

        # Audit rows record the ACTING admin as user_id and the affected
        # student by username in target/username_attempted (confirmed via
        # direct inspection of app/crud/audit.write_audit_event and real
        # rows in the DB) — so the "no audit row yet" / "exactly one audit
        # row now" checks below key on `target`, not `user_id`.
        audit_count_before = self.db.scalar(
            select(AuthAuditLog.id).where(AuthAuditLog.target == student.username).limit(1)
        )
        self.assertIsNone(audit_count_before)

        result = delete_student(student.id, make_request(), db=self.db, actor=self.actor)
        self.assertEqual(result.action, "deactivated")

        self.db.expire_all()
        reloaded = self.db.get(User, student.id)
        self.assertFalse(reloaded.is_active)

        session_row = self.db.get(AuthSession, session_id)
        self.assertIsNotNone(session_row.revoked_at)  # soft-revoked, not deleted — same as before this fix

        audit_row = self.db.scalar(select(AuthAuditLog).where(AuthAuditLog.target == student.username))
        self.assertIsNotNone(audit_row)
        self.assertEqual(audit_row.event_type.value, "student_disabled")
        self.assertEqual(audit_row.user_id, self.actor.id)  # recorded as the acting admin, not the student

        ticket_still_there = self.db.scalar(select(SupportTicket).where(SupportTicket.requester_id == student.id))
        self.assertIsNotNone(ticket_still_there)  # historical record preserved

    def test_deactivated_student_cannot_log_in(self):
        student = self._make_student("loginblock")
        self.db.add(SupportTicket(
            requester_id=student.id, ticket_number=f"TKT-{student.id}-b", subject="x",
            status=TicketStatusEnum.open, priority=TicketPriorityEnum.medium, category=TicketCategoryEnum.other,
        ))
        self.db.commit()

        delete_student(student.id, make_request(), db=self.db, actor=self.actor)

        from unittest.mock import patch
        from app.api import routes_auth

        payload = SimpleNamespace(username=student.username, password="Integration#Test1", portal="student")
        with patch.object(routes_auth, "get_settings", return_value=SimpleNamespace(maintenance_mode=False)), \
             patch.object(routes_auth, "write_audit_event"):
            with self.assertRaises(HTTPException) as ctx:
                login(make_request(), payload, db=self.db)
        self.assertEqual(ctx.exception.status_code, 401)

    def test_repeated_delete_requests_are_idempotent_against_real_db(self):
        student = self._make_student("repeat")
        self.db.add(SupportTicket(
            requester_id=student.id, ticket_number=f"TKT-{student.id}-r", subject="x",
            status=TicketStatusEnum.open, priority=TicketPriorityEnum.medium, category=TicketCategoryEnum.other,
        ))
        self.db.commit()

        first = delete_student(student.id, make_request(), db=self.db, actor=self.actor)
        second = delete_student(student.id, make_request(), db=self.db, actor=self.actor)
        self.assertEqual(first.action, "deactivated")
        self.assertEqual(second.action, "deactivated")

        audit_rows = self.db.scalars(select(AuthAuditLog).where(AuthAuditLog.target == student.username)).all()
        self.assertEqual(len(audit_rows), 1)  # no duplicate audit event on the second, no-op call

    def test_nonexistent_student_is_404_against_real_db(self):
        with self.assertRaises(HTTPException) as ctx:
            delete_student(999999999, make_request(), db=self.db, actor=self.actor)
        self.assertEqual(ctx.exception.status_code, 404)

    # ---------- the actual regression: transaction atomicity ----------
    def test_final_commit_failure_rolls_back_the_entire_transaction(self):
        """THE regression test for the bug this fix addresses. Before the
        fix: write_audit_event's own internal commit silently persisted
        is_active=False before this simulated failure, so the row stayed
        deactivated even though delete_student raised. After the fix: the
        audit entry is staged (commit=False) and only committed together
        with is_active and the session revocation in ONE commit — so this
        failure must revert all of it."""
        student = self._make_student("rollback")
        self.db.add(SupportTicket(
            requester_id=student.id, ticket_number=f"TKT-{student.id}-rb", subject="x",
            status=TicketStatusEnum.open, priority=TicketPriorityEnum.medium, category=TicketCategoryEnum.other,
        ))
        self.db.commit()

        real_commit = self.db.commit
        def failing_commit():
            raise RuntimeError("simulated commit failure")
        self.db.commit = failing_commit
        try:
            with self.assertRaises(RuntimeError):
                delete_student(student.id, make_request(), db=self.db, actor=self.actor)
        finally:
            self.db.commit = real_commit

        self.db.expire_all()
        reloaded = self.db.get(User, student.id)
        self.assertTrue(reloaded.is_active, "deactivation must NOT have persisted after a failed commit")
        audit_row = self.db.scalar(select(AuthAuditLog).where(AuthAuditLog.target == student.username))
        self.assertIsNone(audit_row, "the audit entry must NOT have persisted either — same transaction")

    def test_audit_writing_failure_leaves_no_partial_deactivation(self):
        """A failure constructing/staging the audit entry itself (distinct
        from a failure at final commit time) must also leave nothing
        persisted, since it happens before delete_student's own commit is
        ever reached."""
        student = self._make_student("auditfail")
        self.db.add(SupportTicket(
            requester_id=student.id, ticket_number=f"TKT-{student.id}-af", subject="x",
            status=TicketStatusEnum.open, priority=TicketPriorityEnum.medium, category=TicketCategoryEnum.other,
        ))
        self.db.commit()

        from unittest.mock import patch

        from app.api import routes_admin_students as students_module
        with patch.object(students_module, "write_audit_event", side_effect=RuntimeError("simulated audit failure")):
            with self.assertRaises(RuntimeError):
                delete_student(student.id, make_request(), db=self.db, actor=self.actor)

        self.db.rollback()
        self.db.expire_all()
        reloaded = self.db.get(User, student.id)
        self.assertTrue(reloaded.is_active, "deactivation must not persist when the audit write itself fails")

    def test_session_revocation_failure_rolls_back_before_any_commit(self):
        student = self._make_student("revokefail")
        self.db.add(SupportTicket(
            requester_id=student.id, ticket_number=f"TKT-{student.id}-rf", subject="x",
            status=TicketStatusEnum.open, priority=TicketPriorityEnum.medium, category=TicketCategoryEnum.other,
        ))
        self.db.commit()

        from unittest.mock import patch

        from app.api import routes_admin_students as students_module
        with patch.object(students_module, "revoke_all_sessions", side_effect=RuntimeError("simulated revoke failure")):
            with self.assertRaises(RuntimeError):
                delete_student(student.id, make_request(), db=self.db, actor=self.actor)

        self.db.rollback()
        self.db.expire_all()
        reloaded = self.db.get(User, student.id)
        self.assertTrue(reloaded.is_active, "nothing should commit if session revocation itself fails")

    # ---------- existing audit functionality elsewhere: default commit=True unaffected ----------
    def test_default_write_audit_event_still_commits_independently_against_real_db(self):
        """Every OTHER call site in the codebase (67 of them) relies on
        write_audit_event's default commit=True behavior being completely
        unchanged. This confirms it against a real DB, not just a mock."""
        from app.crud.audit import write_audit_event
        from app.models.audit_log import AuthEventType

        student = self._make_student("defaultcommit")
        ip, ua = "127.0.0.1", "integration-test"
        write_audit_event(self.db, AuthEventType.login_success, ip, ua, user=student)

        # A SEPARATE session must see this row, proving it was really
        # committed (not just staged in self.db's own identity map).
        with Session(bind=engine) as other_db:
            row = other_db.scalar(select(AuthAuditLog).where(AuthAuditLog.user_id == student.id))
            self.assertIsNotNone(row)
            self.assertEqual(row.event_type.value, "login_success")


if __name__ == "__main__":
    unittest.main()
