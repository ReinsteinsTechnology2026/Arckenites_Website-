"""Unit tests for RBAC allow/deny behavior: require_role and
require_permission (app/core/deps.py), plus the permission lookup they
rely on (app/crud/permissions.user_has_permission). Uses lightweight fake
user/db objects — no real database, no real HTTP request.
"""

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from fastapi import HTTPException

from app.core.deps import require_permission, require_role
from app.crud import permissions as permissions_module
from app.models.admin_role import SUPER_ADMIN_SLUG
from app.models.user import RoleEnum


def make_user(**overrides):
    defaults = dict(role=RoleEnum.admin, admin_role=None, admin_role_id=None)
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class TestRequireRole(unittest.TestCase):
    def test_allows_a_user_with_an_allowed_role(self):
        dependency = require_role("admin", "staff")
        user = make_user(role=RoleEnum.staff)
        self.assertIs(dependency(user=user), user)

    def test_denies_a_user_without_an_allowed_role(self):
        dependency = require_role("admin")
        user = make_user(role=RoleEnum.student)
        with self.assertRaises(HTTPException) as ctx:
            dependency(user=user)
        self.assertEqual(ctx.exception.status_code, 403)


class TestUserHasPermission(unittest.TestCase):
    """The real permission-lookup function, with a mocked db for the one
    path that would otherwise hit the database."""

    def test_non_admin_never_has_any_permission(self):
        db = MagicMock()
        user = make_user(role=RoleEnum.student, admin_role=SimpleNamespace(slug="anything"))
        self.assertFalse(permissions_module.user_has_permission(db, user, "students.view"))
        db.scalar.assert_not_called()

    def test_admin_with_no_admin_role_assigned_has_no_permissions(self):
        db = MagicMock()
        user = make_user(role=RoleEnum.admin, admin_role=None)
        self.assertFalse(permissions_module.user_has_permission(db, user, "students.view"))

    def test_super_admin_has_every_permission_without_a_db_lookup(self):
        db = MagicMock()
        user = make_user(admin_role=SimpleNamespace(slug=SUPER_ADMIN_SLUG), admin_role_id=1)
        self.assertTrue(permissions_module.user_has_permission(db, user, "anything.at_all"))
        db.scalar.assert_not_called()

    def test_regular_admin_role_is_granted_only_when_the_row_exists(self):
        user = make_user(admin_role=SimpleNamespace(slug="leads_admin"), admin_role_id=7)

        db_with_grant = MagicMock()
        db_with_grant.scalar.return_value = 1  # a matching AdminRolePermission.id was found
        self.assertTrue(permissions_module.user_has_permission(db_with_grant, user, "leads.view"))

        db_without_grant = MagicMock()
        db_without_grant.scalar.return_value = None
        self.assertFalse(permissions_module.user_has_permission(db_without_grant, user, "leads.delete"))


class TestRequirePermission(unittest.TestCase):
    """require_permission composes require_role("admin") with
    user_has_permission — exercised end-to-end through its real
    dependency function."""

    def test_non_admin_is_denied_before_any_permission_check(self):
        dependency = require_permission("students.view")
        user = make_user(role=RoleEnum.staff)
        db = MagicMock()
        with self.assertRaises(HTTPException) as ctx:
            dependency(user=user, db=db)
        self.assertEqual(ctx.exception.status_code, 403)

    def test_admin_without_the_specific_permission_is_denied(self):
        dependency = require_permission("students.delete")
        user = make_user(admin_role=SimpleNamespace(slug="support_admin"), admin_role_id=2)
        db = MagicMock()
        db.scalar.return_value = None
        with self.assertRaises(HTTPException) as ctx:
            dependency(user=user, db=db)
        self.assertEqual(ctx.exception.status_code, 403)

    def test_admin_with_the_specific_permission_is_allowed(self):
        dependency = require_permission("students.delete")
        user = make_user(admin_role=SimpleNamespace(slug="support_admin"), admin_role_id=2)
        db = MagicMock()
        db.scalar.return_value = 99
        self.assertIs(dependency(user=user, db=db), user)

    def test_super_admin_is_allowed_for_any_permission_key(self):
        dependency = require_permission("lab_vm.manage_vms")
        user = make_user(admin_role=SimpleNamespace(slug=SUPER_ADMIN_SLUG), admin_role_id=1)
        db = MagicMock()
        self.assertIs(dependency(user=user, db=db), user)
        db.scalar.assert_not_called()


if __name__ == "__main__":
    unittest.main()
