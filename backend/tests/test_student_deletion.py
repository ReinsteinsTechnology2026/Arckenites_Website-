"""Unit tests for DELETE /admin/students/{id} (app/api/routes_admin_students
.delete_student) and the login-blocks-inactive-students behavior it depends
on (app/api/routes_auth.login).

Root cause under test: the old delete_student always attempted a hard
delete, detaching/clearing an INCOMPLETE list of tables that reference
users.id before calling db.delete(user). Any student with a row in a table
outside that list (support tickets, batch chat, lab VM access, meetings,
linked Google identity, ...) made the final db.commit() hit an unhandled
foreign-key violation, which was caught and turned into the generic
"Student has related records that prevent deletion. Contact engineering."
409 the admin saw.

The fix: check every known users.id-referencing table first
(_student_has_related_records). Any match -> deactivate (is_active=False,
sessions revoked) instead of touching those records. Zero matches -> the
hard delete proceeds exactly as before, still with an IntegrityError safety
net.

Uses lightweight fake user objects (SimpleNamespace) and a MagicMock
Session — no real database, matching the style of test_rbac.py /
test_otp.py / test_google_auth.py. No pytest; stdlib unittest only.
"""

import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

from app.api import routes_admin_students as students_module
from app.models.user import RoleEnum


def make_student(**overrides):
    defaults = dict(
        id=42,
        username="john.doe",
        full_name="John Doe",
        role=RoleEnum.student,
        is_active=True,
        photo_url=None,
        program=None,
        phone=None,
        email=None,
        must_change_password=False,
        created_at=datetime.now(timezone.utc),
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def make_actor():
    return SimpleNamespace(
        id=1, username="admin1", full_name="Admin One", role=RoleEnum.admin,
        admin_role=None, admin_role_id=None,
    )


def make_request():
    return SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"), headers={"user-agent": "pytest"})


def make_starlette_request():
    """login() is wrapped by slowapi's rate limiter, which insists its
    `request` argument is a real starlette Request (isinstance check) rather
    than a SimpleNamespace double — build the smallest real one that works
    without a running ASGI server."""
    from starlette.requests import Request as StarletteRequest

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/auth/login",
        "headers": [(b"user-agent", b"pytest")],
        "client": ("127.0.0.1", 0),
        "query_string": b"",
    }
    return StarletteRequest(scope)


class TestStudentHasRelatedRecords(unittest.TestCase):
    def test_false_when_every_table_is_empty(self):
        db = MagicMock()
        db.scalar.return_value = None
        self.assertFalse(students_module._student_has_related_records(db, 42))

    def test_true_when_any_single_table_has_a_row(self):
        db = MagicMock()
        db.scalar.return_value = 1  # first check (BatchEnrollment) matches
        self.assertTrue(students_module._student_has_related_records(db, 42))

    def test_true_when_only_an_empty_conversation_exists(self):
        """A student who has a Conversation row (chat started) but sent no
        DirectMessage yet still counts as having a related record — this is
        the extra belt-and-suspenders check after the table loop."""
        db = MagicMock()
        # Every per-table EXISTS check returns None, but the dedicated
        # conversation-participation check (last scalar() call) returns a hit.
        db.scalar.side_effect = [None] * len(students_module._STUDENT_RELATED_RECORD_CHECKS) + [99]
        self.assertTrue(students_module._student_has_related_records(db, 42))


class TestDeleteStudentAuthorization(unittest.TestCase):
    """delete_student has no auth logic of its own — it delegates entirely
    to its `actor: User = Depends(require_permission("students.delete"))`
    parameter (require_permission's own allow/deny behavior is unit-tested
    directly in test_rbac.py). This confirms delete_student is actually
    wired to that exact dependency, so an unauthorized caller is rejected
    before delete_student's body — and therefore any DB mutation — ever
    runs."""

    def _delete_student_actor_dependency(self):
        import inspect

        sig = inspect.signature(students_module.delete_student)
        return sig.parameters["actor"].default.dependency

    def test_caller_without_students_delete_permission_is_denied(self):
        dependency = self._delete_student_actor_dependency()
        unauthorized_actor = SimpleNamespace(role=RoleEnum.staff, admin_role=None, admin_role_id=None)
        db = MagicMock()
        with self.assertRaises(HTTPException) as ctx:
            dependency(user=unauthorized_actor, db=db)
        self.assertEqual(ctx.exception.status_code, 403)

    def test_caller_with_students_delete_permission_is_allowed(self):
        dependency = self._delete_student_actor_dependency()
        authorized_actor = SimpleNamespace(
            role=RoleEnum.admin, admin_role=SimpleNamespace(slug="support_admin"), admin_role_id=2,
        )
        db = MagicMock()
        db.scalar.return_value = 99  # a matching AdminRolePermission row exists
        self.assertIs(dependency(user=authorized_actor, db=db), authorized_actor)


class TestDeleteStudent(unittest.TestCase):
    def test_nonexistent_student_is_404(self):
        db = MagicMock()
        db.get.return_value = None
        with self.assertRaises(HTTPException) as ctx:
            students_module.delete_student(999, make_request(), db=db, actor=make_actor())
        self.assertEqual(ctx.exception.status_code, 404)

    def test_student_with_no_related_records_is_hard_deleted(self):
        db = MagicMock()
        student = make_student()
        db.get.return_value = student
        db.scalar.return_value = None  # no related records anywhere

        result = students_module.delete_student(42, make_request(), db=db, actor=make_actor())

        self.assertEqual(result.action, "deleted")
        self.assertIsNone(result.student)
        db.delete.assert_called_once_with(student)
        self.assertFalse(student is None)

    def test_student_with_related_records_is_deactivated_not_deleted(self):
        db = MagicMock()
        student = make_student(is_active=True)
        db.get.return_value = student
        db.scalar.return_value = 1  # BatchEnrollment row found -> related records exist

        with patch.object(students_module, "revoke_all_sessions") as mock_revoke:
            result = students_module.delete_student(42, make_request(), db=db, actor=make_actor())

        self.assertEqual(result.action, "deactivated")
        self.assertIsNotNone(result.student)
        self.assertFalse(student.is_active)
        mock_revoke.assert_called_once_with(db, student.id)
        db.delete.assert_not_called()  # never hard-deleted

    def test_repeated_delete_requests_are_idempotent(self):
        """Calling delete twice on a student with related records: the first
        call deactivates and logs it, the second call (student already
        inactive) must not spam a second audit event, and must still report
        the account as deactivated rather than erroring."""
        db = MagicMock()
        student = make_student(is_active=True)
        db.get.return_value = student
        db.scalar.return_value = 1

        with patch.object(students_module, "revoke_all_sessions"), \
             patch.object(students_module, "write_audit_event") as mock_audit:
            first = students_module.delete_student(42, make_request(), db=db, actor=make_actor())
            self.assertEqual(mock_audit.call_count, 1)  # is_active flipped true->false

            second = students_module.delete_student(42, make_request(), db=db, actor=make_actor())
            self.assertEqual(mock_audit.call_count, 1)  # no new transition, no new event

        self.assertEqual(first.action, "deactivated")
        self.assertEqual(second.action, "deactivated")

    def test_db_failure_during_hard_delete_rolls_back_and_returns_409(self):
        db = MagicMock()
        student = make_student()
        db.get.return_value = student
        db.scalar.return_value = None  # eligible for hard delete
        db.commit.side_effect = IntegrityError("DELETE FROM users", {}, Exception("fk violation"))

        with self.assertRaises(HTTPException) as ctx:
            students_module.delete_student(42, make_request(), db=db, actor=make_actor())

        self.assertEqual(ctx.exception.status_code, 409)
        db.rollback.assert_called_once()

    def test_db_failure_during_deactivation_rolls_back(self):
        db = MagicMock()
        student = make_student(is_active=False)  # already inactive -> single commit call
        db.get.return_value = student
        db.scalar.return_value = 1  # has related records -> deactivation path
        db.commit.side_effect = Exception("connection lost")

        with patch.object(students_module, "revoke_all_sessions"):
            with self.assertRaises(Exception):
                students_module.delete_student(42, make_request(), db=db, actor=make_actor())

        db.rollback.assert_called_once()

    def test_deactivation_writes_the_audit_event_without_committing_it_separately(self):
        """Regression test for the transaction-boundary bug: write_audit_event
        must be called with commit=False here, so the audit row, is_active,
        and the session revocation all land in the SAME db.commit() — not an
        earlier, separate one that would already be durable before the
        function's own commit/rollback logic ever runs. (The real failure
        mode — an already-active student whose deactivation silently
        persisted despite the function appearing to raise — could not be
        seen by a mocked Session's commit(), since mocking it doesn't
        actually flush anything; that's why this is asserted directly via
        the call arguments here, and separately re-verified against a real
        PostgreSQL transaction in test_student_deletion_integration.py.)"""
        db = MagicMock()
        student = make_student(is_active=True)
        db.get.return_value = student
        db.scalar.return_value = 1

        with patch.object(students_module, "revoke_all_sessions"), \
             patch.object(students_module, "write_audit_event") as mock_audit:
            students_module.delete_student(42, make_request(), db=db, actor=make_actor())

        self.assertEqual(mock_audit.call_args.kwargs.get("commit"), False)
        # Exactly one commit for the whole branch — not one from the audit
        # helper and a second one from delete_student itself.
        self.assertEqual(db.commit.call_count, 1)

    def test_commit_failure_after_a_real_audit_write_still_rolls_back_everything(self):
        """Uses the REAL write_audit_event (not mocked) with a MagicMock
        Session, so this at least confirms write_audit_event(commit=False)
        does not call db.commit() itself — the strongest statement a mocked
        Session can make about this fix. Genuine transactional atomicity
        (that a failure afterward actually reverts the is_active flip in a
        real database) is verified separately against real PostgreSQL in
        test_student_deletion_integration.py, since a mocked commit() can't
        prove anything about real flush/rollback semantics."""
        db = MagicMock()
        student = make_student(is_active=True)
        db.get.return_value = student
        db.scalar.return_value = 1
        db.commit.side_effect = Exception("commit failed after staging everything")

        with patch.object(students_module, "revoke_all_sessions"):
            with self.assertRaises(Exception):
                students_module.delete_student(42, make_request(), db=db, actor=make_actor())

        # write_audit_event(commit=False) must not have committed on its own
        # before delete_student's own (failing) commit ran.
        commit_calls_before_failure = db.commit.call_count
        self.assertEqual(commit_calls_before_failure, 1)  # only delete_student's own attempt
        db.rollback.assert_called_once()

    def test_session_revocation_failure_rolls_back_before_any_commit(self):
        """If revoke_all_sessions itself raises, delete_student must not
        reach db.commit() at all — the exception propagates before the
        commit/rollback try block, so nothing (is_active, audit entry) was
        ever staged for commit in the first place."""
        db = MagicMock()
        student = make_student(is_active=True)
        db.get.return_value = student
        db.scalar.return_value = 1

        with patch.object(students_module, "revoke_all_sessions", side_effect=RuntimeError("revoke failed")):
            with self.assertRaises(RuntimeError):
                students_module.delete_student(42, make_request(), db=db, actor=make_actor())

        db.commit.assert_not_called()


class TestDeactivatedStudentCannotLogIn(unittest.TestCase):
    """Confirms the other half of the fix: deactivation must actually block
    login, not just hide the account from the admin list. routes_auth.login
    already has this check (line ~67: `if not user.is_active: ... 401`) —
    this test exercises that real code path for a student deactivated via
    the delete-becomes-deactivate flow above."""

    def test_inactive_student_login_is_rejected(self):
        from app.api import routes_auth

        inactive_student = make_student(is_active=False)
        inactive_student.password_hash = "irrelevant"
        inactive_student.failed_login_count = 0
        inactive_student.locked_until = None

        db = MagicMock()
        payload = SimpleNamespace(username="john.doe", password="whatever")

        with patch.object(routes_auth, "get_settings", return_value=SimpleNamespace(maintenance_mode=False)), \
             patch.object(routes_auth, "get_by_username", return_value=inactive_student), \
             patch.object(routes_auth, "is_locked", return_value=False), \
             patch.object(routes_auth, "write_audit_event"):
            with self.assertRaises(HTTPException) as ctx:
                routes_auth.login(make_starlette_request(), payload, db=db)

        self.assertEqual(ctx.exception.status_code, 401)


if __name__ == "__main__":
    unittest.main()
