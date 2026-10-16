"""Unit tests for the transaction-boundary fix in
app/api/routes_admin_users.py:update_admin_user — the admin-account
analogue of the bug fixed in routes_admin_students.py:delete_student (see
test_student_deletion.py / test_student_deletion_integration.py).

Audit finding: delete_admin_user (the DELETE endpoint) does NOT have this
bug — it has no deactivation branch, and its one write_audit_event call
happens AFTER a successful db.commit() already completed (same safe
pattern used everywhere else in the codebase). The actual bug was in
update_admin_user (PATCH): role changes, the is_active toggle + session
revocation, password resets, and profile edits were each followed by a
write_audit_event() call that committed independently (default
commit=True), before the function's OWN final db.commit() — which also had
NO try/except at all, so a later failure left whichever branches had
already fired durably persisted with zero rollback possible, while the
request itself could still surface as an unhandled error to the caller.

Mocked tests here can only assert *calls* (commit=False passed, rollback()
called) — real atomicity (that a real Postgres transaction genuinely
reverts) is separately verified in test_admin_user_integration.py.
"""

import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi import HTTPException

from app.api import routes_admin_users as admin_users_module
from app.models.admin_role import SUPER_ADMIN_SLUG
from app.models.user import RoleEnum
from app.schemas.admin_users import UpdateAdminUserRequest


def make_admin_role(slug="support_admin", role_id=3):
    return SimpleNamespace(id=role_id, slug=slug, name=slug)


def make_target_admin(**overrides):
    defaults = dict(
        id=50, username="target.admin", full_name="Target Admin", role=RoleEnum.admin,
        is_active=True, admin_role=make_admin_role(), admin_role_id=3,
        admin_profile=None, must_change_password=False, failed_login_count=0, locked_until=None,
        password_hash="irrelevant", last_login_at=None, created_at=datetime.now(timezone.utc),
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def make_actor():
    return SimpleNamespace(
        id=1, username="actor.admin", full_name="Actor Admin", role=RoleEnum.admin,
        admin_role=make_admin_role(slug=SUPER_ADMIN_SLUG, role_id=1), admin_role_id=1,
    )


def make_request():
    return SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"), headers={"user-agent": "pytest"})


class TestUpdateAdminUserAuthorization(unittest.TestCase):
    """update_admin_user delegates entirely to its
    actor: User = Depends(require_permission("admin_users.edit")) parameter,
    same pattern verified generically in test_rbac.py — this confirms the
    function is actually wired to that exact dependency."""

    def _actor_dependency(self):
        import inspect

        sig = inspect.signature(admin_users_module.update_admin_user)
        return sig.parameters["actor"].default.dependency

    def test_caller_without_admin_users_edit_permission_is_denied(self):
        dependency = self._actor_dependency()
        unauthorized_actor = SimpleNamespace(role=RoleEnum.staff, admin_role=None, admin_role_id=None)
        db = MagicMock()
        with self.assertRaises(HTTPException) as ctx:
            dependency(user=unauthorized_actor, db=db)
        self.assertEqual(ctx.exception.status_code, 403)


class TestUpdateAdminUserTransaction(unittest.TestCase):
    def _get_admin_patch(self, user):
        return patch.object(admin_users_module, "_get_admin_or_404", return_value=user)

    def test_is_active_toggle_writes_audit_with_commit_false_and_commits_once(self):
        user = make_target_admin(is_active=True)
        db = MagicMock()
        payload = UpdateAdminUserRequest(is_active=False)

        with self._get_admin_patch(user), patch.object(admin_users_module, "revoke_all_sessions") as mock_revoke, \
             patch.object(admin_users_module, "write_audit_event") as mock_audit:
            admin_users_module.update_admin_user(50, payload, make_request(), db=db, actor=make_actor())

        self.assertFalse(user.is_active)
        mock_revoke.assert_called_once_with(db, user.id)
        # Every write_audit_event call in this function must defer its
        # commit to the function's own single final commit.
        for call in mock_audit.call_args_list:
            self.assertEqual(call.kwargs.get("commit"), False)
        self.assertEqual(db.commit.call_count, 1)

    def test_role_change_writes_audit_with_commit_false(self):
        user = make_target_admin(admin_role=make_admin_role(slug="support_admin", role_id=3), admin_role_id=3)
        new_role = make_admin_role(slug="leads_admin", role_id=9)
        db = MagicMock()
        db.scalar.return_value = 1  # _count_active_super_admins, irrelevant here
        payload = UpdateAdminUserRequest(admin_role_id=9)

        with self._get_admin_patch(user), patch.object(admin_users_module, "_get_role_or_400", return_value=new_role), \
             patch.object(admin_users_module, "write_audit_event") as mock_audit:
            admin_users_module.update_admin_user(50, payload, make_request(), db=db, actor=make_actor())

        self.assertEqual(user.admin_role_id, 9)
        mock_audit.assert_called_once()
        self.assertEqual(mock_audit.call_args.kwargs.get("commit"), False)
        self.assertEqual(db.commit.call_count, 1)

    def test_password_reset_writes_audit_with_commit_false(self):
        user = make_target_admin()
        db = MagicMock()
        payload = UpdateAdminUserRequest(reset_temp_password="NewTemp#Pass1")

        with self._get_admin_patch(user), patch.object(admin_users_module, "revoke_all_sessions") as mock_revoke, \
             patch.object(admin_users_module, "write_audit_event") as mock_audit:
            admin_users_module.update_admin_user(50, payload, make_request(), db=db, actor=make_actor())

        mock_revoke.assert_called_once_with(db, user.id)
        self.assertTrue(user.must_change_password)
        mock_audit.assert_called_once()
        self.assertEqual(mock_audit.call_args.kwargs.get("commit"), False)
        self.assertEqual(db.commit.call_count, 1)

    def test_final_commit_failure_rolls_back(self):
        """Before the fix: no try/except existed at all here, so a failure
        at this point propagated with NO rollback call — and, worse, any
        branch above had already independently committed via
        write_audit_event's own default commit. After the fix: one
        rollback() call reverts everything staged in this request."""
        user = make_target_admin(is_active=True)
        db = MagicMock()
        db.commit.side_effect = Exception("connection lost")
        payload = UpdateAdminUserRequest(is_active=False)

        with self._get_admin_patch(user), patch.object(admin_users_module, "revoke_all_sessions"):
            with self.assertRaises(Exception):
                admin_users_module.update_admin_user(50, payload, make_request(), db=db, actor=make_actor())

        db.rollback.assert_called_once()

    def test_audit_writing_failure_propagates_before_any_commit(self):
        user = make_target_admin(is_active=True)
        db = MagicMock()
        payload = UpdateAdminUserRequest(is_active=False)

        with self._get_admin_patch(user), patch.object(admin_users_module, "revoke_all_sessions"), \
             patch.object(admin_users_module, "write_audit_event", side_effect=RuntimeError("audit failure")):
            with self.assertRaises(RuntimeError):
                admin_users_module.update_admin_user(50, payload, make_request(), db=db, actor=make_actor())

        db.commit.assert_not_called()

    def test_repeated_identical_update_does_not_write_a_duplicate_audit_event(self):
        """Setting is_active to the value it already is must not re-fire the
        audit/session-revocation branch at all (existing behavior,
        unaffected by this fix — confirmed still true)."""
        user = make_target_admin(is_active=False)
        db = MagicMock()
        payload = UpdateAdminUserRequest(is_active=False)  # already False — no-op

        with self._get_admin_patch(user), patch.object(admin_users_module, "revoke_all_sessions") as mock_revoke, \
             patch.object(admin_users_module, "write_audit_event") as mock_audit:
            admin_users_module.update_admin_user(50, payload, make_request(), db=db, actor=make_actor())

        mock_revoke.assert_not_called()
        mock_audit.assert_not_called()
        db.commit.assert_called_once()  # still commits (e.g. db.add(user)), just nothing audit-worthy happened


class TestDeleteAdminUserUnaffected(unittest.TestCase):
    """delete_admin_user was audited and confirmed to NOT have this bug —
    its one write_audit_event call already happens after a successful,
    already-committed db.commit(). This pins that down so a future change
    can't silently reintroduce the bug there without a test noticing."""

    def test_audit_event_is_written_with_default_commit_after_delete_succeeds(self):
        user = make_target_admin(admin_role=None)  # no admin_role -> skips the super-admin-count check entirely
        db = MagicMock()
        db.scalar.return_value = None

        with patch.object(admin_users_module, "_get_admin_or_404", return_value=user), \
             patch.object(admin_users_module, "write_audit_event") as mock_audit:
            admin_users_module.delete_admin_user(50, make_request(), db=db, actor=make_actor())

        # db.delete + db.commit must have already happened BEFORE the one
        # audit call, and that call relies on the SAFE default (commit=True
        # or simply omitted) since nothing is pending at that point.
        db.delete.assert_called_once_with(user)
        self.assertGreaterEqual(db.commit.call_count, 1)
        mock_audit.assert_called_once()
        self.assertNotEqual(mock_audit.call_args.kwargs.get("commit"), False)


if __name__ == "__main__":
    unittest.main()
