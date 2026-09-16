"""add Leads Database Admin role

Revision ID: 8a2f4c6e9b1d
Revises: 7f9c63c4ea71
Create Date: 2026-09-16 00:00:00.000000

Creates only the admin_roles row itself, matching this project's convention
(see b2c3d4e5f6a7): migrations create the fixed-role schema data, seed.py
seeds the permission catalog + default grants for it (idempotent — see
DEFAULT_GRANTS["leads_admin"] in app/crud/permissions.py). Account creation
for this role is further restricted to Super Admin actors only in
routes_admin_users.py — that's an application-level check, not a DB
constraint, so there's nothing to migrate for it here.
"""
from typing import Sequence, Union

from alembic import op


revision: str = '8a2f4c6e9b1d'
down_revision: Union[str, None] = '7f9c63c4ea71'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO admin_roles (name, slug, description, is_system)
        VALUES (
            'Leads Database Admin',
            'leads_admin',
            'Manages the leads database and has read-only access to the student and trainer databases. Can only be assigned by a Super Admin.',
            true
        )
        """
    )


def downgrade() -> None:
    op.execute("DELETE FROM admin_role_permissions WHERE admin_role_id = (SELECT id FROM admin_roles WHERE slug = 'leads_admin')")
    op.execute("DELETE FROM admin_roles WHERE slug = 'leads_admin'")
