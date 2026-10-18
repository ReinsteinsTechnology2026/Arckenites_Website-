"""REAL PostgreSQL integration tests for the transaction-boundary fix in
app/api/routes_admin_users.py:update_admin_user. See
test_admin_user_transactions.py (mocked) for the full bug writeup — this
file proves the same fix against a real Session/transaction, the same way
test_student_deletion_integration.py does for delete_student.

Runs against whatever database backend/.env's DATABASE_URL points at (the
project's own local dev Postgres via backend/docker-compose.yml) — never
production. SKIPPED ENTIRELY (not failed) if unreachable.
"""

import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock
from datetime import datetime, timezone
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import delete as sa_delete
from sqlalchemy import select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.api import routes_admin_users as admin_users_module
from app.api.routes_admin_users import _count_active_super_admins, delete_admin_user, update_admin_user
from app.core.security import hash_password
from app.crud.user import generate_admin_username
from app.database import engine
from app.models.admin_profile import AdminProfile
from app.models.admin_role import AdminRole
from app.models.audit_log import AuthAuditLog
from app.models.session import AuthSession
from app.models.user import RoleEnum, User
from app.schemas.admin_users import UpdateAdminUserRequest
from postgres_safety import isolated_postgres_enabled


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
        "type": "http", "method": "PATCH", "path": "/admin/admin-users/x",
        "headers": [(b"user-agent", b"integration-test")], "client": ("127.0.0.1", 0), "query_string": b"",
    }
    return StarletteRequest(scope)


@unittest.skipUnless(_db_reachable(), "No local PostgreSQL reachable at DATABASE_URL — skipping real-DB tests")
class TestAdminUserUpdateIntegration(unittest.TestCase):
    def setUp(self):
        self.db = Session(bind=engine)
        self.actor = self.db.scalar(select(User).where(User.username == "admin1@arckenites.com"))
        if self.actor is None:
            self.skipTest("Expected seed admin1@arckenites.com not found in this local DB")
        self._created_user_ids: list[int] = []

    def tearDown(self):
        self.db.rollback()
        for uid in self._created_user_ids:
            user = self.db.get(User, uid)
            if user is None:
                continue
            self.db.execute(update(AuthAuditLog).where(AuthAuditLog.user_id == uid).values(user_id=None))
            self.db.execute(sa_delete(AuthSession).where(AuthSession.user_id == uid))
            self.db.execute(sa_delete(AdminProfile).where(AdminProfile.user_id == uid))
            self.db.delete(user)
        # Same reasoning as test_student_deletion_integration.py's teardown:
        # audit rows naming a test admin as `target` (the normal case — the
        # acting Super Admin, not the test subject, is user_id) must be
        # purged separately or they accumulate across repeated runs.
        self.db.execute(sa_delete(AuthAuditLog).where(AuthAuditLog.target.ilike("%integtestadmin%")))
        self.db.commit()
        self.db.close()

    def _make_target_admin(self, suffix: str) -> User:
        role = self.db.scalar(select(AdminRole).where(AdminRole.slug == "support_admin"))
        username = generate_admin_username(self.db, f"integtestadmin{suffix}")
        user = User(
            username=username, password_hash=hash_password("Integration#Test1"),
            full_name=f"Integration Test Admin {suffix}", role=RoleEnum.admin, admin_role_id=role.id,
            is_active=True, must_change_password=True,
        )
        self.db.add(user)
        self.db.flush()
        self.db.add(AdminProfile(user_id=user.id))
        self.db.commit()
        self.db.refresh(user)
        self._created_user_ids.append(user.id)
        return user

    def test_is_active_toggle_commits_status_session_and_audit_together(self):
        target = self._make_target_admin("deactivate")
        self.db.add(AuthSession(
            id=__import__("uuid").uuid4(), user_id=target.id,
            expires_at=datetime.now(timezone.utc).replace(year=2099), ip_address="127.0.0.1", user_agent="x",
        ))
        self.db.commit()

        result = update_admin_user(
            target.id, UpdateAdminUserRequest(is_active=False), make_request(), db=self.db, actor=self.actor,
        )
        self.assertFalse(result.is_active)

        self.db.expire_all()
        reloaded = self.db.get(User, target.id)
        self.assertFalse(reloaded.is_active)

        sessions = self.db.scalars(select(AuthSession).where(AuthSession.user_id == target.id)).all()
        self.assertTrue(all(s.revoked_at is not None for s in sessions))

        audit_row = self.db.scalar(select(AuthAuditLog).where(AuthAuditLog.target == target.username))
        self.assertIsNotNone(audit_row)
        self.assertEqual(audit_row.event_type.value, "admin_user_disabled")

    def test_final_commit_failure_rolls_back_everything(self):
        """THE regression test: before the fix, is_active and the session
        revocation would already be durably committed by this point (via
        write_audit_event's own independent commit), so this simulated
        failure would NOT have reverted them. After the fix, one commit
        covers everything, so this rollback genuinely reverts all of it."""
        target = self._make_target_admin("rollback")

        real_commit = self.db.commit
        def failing_commit():
            raise RuntimeError("simulated commit failure")
        self.db.commit = failing_commit
        try:
            with self.assertRaises(RuntimeError):
                update_admin_user(
                    target.id, UpdateAdminUserRequest(is_active=False), make_request(), db=self.db, actor=self.actor,
                )
        finally:
            self.db.commit = real_commit

        self.db.expire_all()
        reloaded = self.db.get(User, target.id)
        self.assertTrue(reloaded.is_active, "deactivation must NOT have persisted after a failed commit")
        audit_row = self.db.scalar(select(AuthAuditLog).where(AuthAuditLog.target == target.username))
        self.assertIsNone(audit_row, "the audit entry must NOT have persisted either — same transaction")

    def test_audit_writing_failure_leaves_no_partial_change(self):
        target = self._make_target_admin("auditfail")

        from unittest.mock import patch

        from app.api import routes_admin_users as admin_users_module
        with patch.object(admin_users_module, "write_audit_event", side_effect=RuntimeError("simulated audit failure")):
            with self.assertRaises(RuntimeError):
                update_admin_user(
                    target.id, UpdateAdminUserRequest(is_active=False), make_request(), db=self.db, actor=self.actor,
                )

        self.db.rollback()
        self.db.expire_all()
        reloaded = self.db.get(User, target.id)
        self.assertTrue(reloaded.is_active, "deactivation must not persist when the audit write itself fails")

    def test_session_revocation_failure_rolls_back_before_any_commit(self):
        target = self._make_target_admin("revokefail")

        from unittest.mock import patch

        from app.api import routes_admin_users as admin_users_module
        with patch.object(admin_users_module, "revoke_all_sessions", side_effect=RuntimeError("simulated revoke failure")):
            with self.assertRaises(RuntimeError):
                update_admin_user(
                    target.id, UpdateAdminUserRequest(is_active=False), make_request(), db=self.db, actor=self.actor,
                )

        self.db.rollback()
        self.db.expire_all()
        reloaded = self.db.get(User, target.id)
        self.assertTrue(reloaded.is_active, "nothing should commit if session revocation itself fails")

    def test_repeated_identical_update_writes_no_duplicate_audit_row(self):
        target = self._make_target_admin("repeat")

        update_admin_user(target.id, UpdateAdminUserRequest(is_active=False), make_request(), db=self.db, actor=self.actor)
        update_admin_user(target.id, UpdateAdminUserRequest(is_active=False), make_request(), db=self.db, actor=self.actor)

        audit_rows = self.db.scalars(select(AuthAuditLog).where(AuthAuditLog.target == target.username)).all()
        self.assertEqual(len(audit_rows), 1)

    def _race_last_two_super_admins(self, operation: str) -> None:
        # These tests run only on an explicitly marked, disposable QA DB.
        self.assertEqual(_count_active_super_admins(self.db), 1)
        first = self._make_target_admin(f"race{operation}a")
        second = self._make_target_admin(f"race{operation}b")
        super_role = self.db.scalar(select(AdminRole).where(AdminRole.slug == "super_admin"))
        support_role = self.db.scalar(select(AdminRole).where(AdminRole.slug == "support_admin"))
        support_role_id = support_role.id
        first.admin_role_id = second.admin_role_id = super_role.id
        self.actor.is_active = False
        self.db.commit()
        first_id, second_id = first.id, second.id
        self.db.rollback()

        # Hold the shared row until BOTH requests have reached the lock. This
        # makes contention reproducible instead of relying on thread timing.
        reached = Event()
        arrival_lock = Lock()
        arrivals = 0
        original_lock = admin_users_module._lock_super_admin_role

        def observed_lock(session):
            nonlocal arrivals
            with arrival_lock:
                arrivals += 1
                if arrivals == 2:
                    reached.set()
            return original_lock(session)

        def attempt(target_id, actor_id):
            with Session(bind=engine) as session:
                actor = session.get(User, actor_id)
                try:
                    if operation == "delete":
                        delete_admin_user(target_id, make_request(), db=session, actor=actor)
                    else:
                        payload = (UpdateAdminUserRequest(is_active=False) if operation == "disable" else
                                   UpdateAdminUserRequest(admin_role_id=support_role_id))
                        update_admin_user(target_id, payload, make_request(), db=session, actor=actor)
                    return target_id, "success"
                except HTTPException as error:
                    return target_id, f"blocked:{error.status_code}"

        try:
            with Session(bind=engine) as controller:
                controller.scalar(select(AdminRole.id).where(AdminRole.slug == "super_admin").with_for_update())
                try:
                    with patch.object(admin_users_module, "_lock_super_admin_role", side_effect=observed_lock):
                        with ThreadPoolExecutor(max_workers=2) as pool:
                            futures = [pool.submit(attempt, first_id, second_id),
                                       pool.submit(attempt, second_id, first_id)]
                            self.assertTrue(reached.wait(20), "both requests must reach the shared role lock")
                            controller.commit()
                            results = [future.result(timeout=30) for future in futures]
                finally:
                    controller.rollback()
            self.assertCountEqual([result for _, result in results], ["success", "blocked:400"])
            blocked_id = next(target_id for target_id, result in results if result == "blocked:400")
            self.db.expire_all()
            blocked = self.db.get(User, blocked_id)
            self.assertIsNotNone(blocked, "a rejected request cannot partially delete its target")
            self.assertTrue(blocked.is_active, "a rejected request cannot partially deactivate its target")
            self.assertEqual(blocked.admin_role.slug, "super_admin")
            self.assertEqual(_count_active_super_admins(self.db), 1)
        finally:
            self.db.rollback()
            seed_admin = self.db.get(User, self.actor.id)
            seed_admin.is_active = True
            self.db.execute(sa_delete(AuthAuditLog).where(
                AuthAuditLog.target.in_([f"admin:{first_id}", f"admin:{second_id}"])))
            self.db.commit()

    def test_concurrent_deactivation_preserves_one_active_super_admin(self):
        self._race_last_two_super_admins("disable")

    def test_concurrent_deletion_preserves_one_active_super_admin(self):
        self._race_last_two_super_admins("delete")

    def test_concurrent_role_change_preserves_one_active_super_admin(self):
        self._race_last_two_super_admins("role")


if __name__ == "__main__":
    unittest.main()
