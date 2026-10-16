"""Unit tests for app/crud/audit.write_audit_event, specifically the
`commit` parameter added to fix the transaction-boundary bug uncovered in
delete_student (see test_student_deletion.py and
test_student_deletion_integration.py). The default (commit=True) must stay
unchanged for the 60+ other existing call sites across the codebase, all of
which rely on this function committing its own audit row immediately as a
deliberate, separate follow-up to their own already-committed change.
"""

import unittest
from unittest.mock import MagicMock

from app.crud.audit import write_audit_event
from app.models.audit_log import AuthEventType


class TestWriteAuditEventCommitParameter(unittest.TestCase):
    def test_default_behavior_is_unchanged_commit_true(self):
        """Every existing caller omits `commit` entirely and expects an
        immediate, independent commit — this must keep working exactly as
        before."""
        db = MagicMock()
        write_audit_event(db, AuthEventType.login_success, "127.0.0.1", "pytest")
        db.add.assert_called_once()
        db.commit.assert_called_once()

    def test_explicit_commit_true_behaves_the_same_as_default(self):
        db = MagicMock()
        write_audit_event(db, AuthEventType.login_success, "127.0.0.1", "pytest", commit=True)
        db.commit.assert_called_once()

    def test_commit_false_stages_the_row_but_never_commits(self):
        db = MagicMock()
        write_audit_event(db, AuthEventType.student_disabled, "127.0.0.1", "pytest", commit=False)
        db.add.assert_called_once()
        db.commit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
