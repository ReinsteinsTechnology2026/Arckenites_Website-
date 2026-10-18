"""Direct API checks for Admin creation and delegated permission scope."""

import unittest

from app.models import AdminRole, AdminRolePermission, Permission, RoleEnum, User
from tests import test_student_creation as fixtures


class AdminCreationDelegationTests(unittest.TestCase):
    def setUp(self):
        self.portal = fixtures.StudentCreationTests()
        self.portal.setUp()
        self.addCleanup(self.portal.tearDown)
        with fixtures.ProvisioningTestSession(self.portal.engine) as db:
            db.add_all([
                AdminRole(id=2, name="Delegating Admin", slug="delegating_admin"),
                AdminRole(id=3, name="Elevated Admin", slug="elevated_admin"),
                Permission(key="admin_users.create", module="Admin Users", action="create"),
                Permission(key="students.view", module="Students", action="view"),
            ])
            db.flush()
            actor = User(username="delegator.synthetic", full_name="Delegator", role=RoleEnum.admin,
                         admin_role_id=2, password_hash=fixtures.hash_password("SyntheticPassword123"))
            db.add(actor)
            db.flush()
            db.add_all([
                AdminRolePermission(admin_role_id=2, permission_key="admin_users.create"),
                AdminRolePermission(admin_role_id=3, permission_key="students.view"),
            ])
            db.commit()
            self.actor_id = actor.id
        self.headers = self.portal._headers(self.actor_id, "admin")

    def create(self):
        return self.portal.client.post("/api/admin/admin-users", json={
            "full_name": "Synthetic New Admin", "admin_role_id": 3,
            "temp_password": "SyntheticPassword123",
        }, headers=self.headers)

    def test_permission_does_not_allow_delegating_broader_role(self):
        response = self.create()
        self.assertEqual(response.status_code, 403, response.text)

    def test_super_admin_can_grant_broader_role(self):
        response = self.portal.client.post("/api/admin/admin-users", json={
            "full_name": "Synthetic New Admin", "admin_role_id": 3,
            "temp_password": "SyntheticPassword123",
        }, headers=self.portal.headers)
        self.assertEqual(response.status_code, 201, response.text)

    def test_delegation_requires_explicit_create_grant(self):
        with fixtures.ProvisioningTestSession(self.portal.engine) as db:
            grant = db.query(AdminRolePermission).filter_by(admin_role_id=2,
                                                              permission_key="admin_users.create").one()
            db.delete(grant)
            db.commit()
        self.assertEqual(self.create().status_code, 403)

    def test_role_editor_cannot_grant_permission_outside_own_scope(self):
        with fixtures.ProvisioningTestSession(self.portal.engine) as db:
            db.add_all([
                Permission(key="roles.edit", module="Roles", action="edit"),
                Permission(key="roles.create", module="Roles", action="create"),
            ])
            db.flush()
            db.add_all([
                AdminRolePermission(admin_role_id=2, permission_key="roles.edit"),
                AdminRolePermission(admin_role_id=2, permission_key="roles.create"),
            ])
            db.commit()
        created = self.portal.client.post("/api/admin/roles", json={
            "name": "Synthetic Role", "permission_keys": ["students.view"],
        }, headers=self.headers)
        self.assertEqual(created.status_code, 403, created.text)
        edited = self.portal.client.patch("/api/admin/roles/3", json={
            "permission_keys": ["students.view", "admin_users.create"],
        }, headers=self.headers)
        self.assertEqual(edited.status_code, 403, edited.text)

    def test_admin_update_cannot_assign_broader_role(self):
        with fixtures.ProvisioningTestSession(self.portal.engine) as db:
            db.add(Permission(key="admin_users.edit", module="Admin Users", action="edit"))
            db.flush()
            db.add(AdminRolePermission(admin_role_id=2, permission_key="admin_users.edit"))
            target = User(username="target.synthetic", full_name="Target", role=RoleEnum.admin,
                          admin_role_id=2, password_hash=fixtures.hash_password("SyntheticPassword123"))
            db.add(target)
            db.commit()
            target_id = target.id
        response = self.portal.client.patch(f"/api/admin/admin-users/{target_id}",
                                            json={"admin_role_id": 3}, headers=self.headers)
        self.assertEqual(response.status_code, 403, response.text)


if __name__ == "__main__":
    unittest.main()
