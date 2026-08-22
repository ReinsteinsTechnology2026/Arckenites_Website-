"""add RBAC tables, users.admin_role_id, activity-log columns, system_settings

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-08-20 00:00:01.000000

Seeds only the minimum needed so the system is never in a "no role
assigned" broken state post-deploy: the 3 fixed-ID system roles and a
backfill of every existing role=admin user to Super Admin. The full
permission catalog and default role grants are seeded by seed.py instead
(matches this project's existing convention: migrations are schema +
minimum-required data, seed.py is the baseline-data mechanism) — this is
safe because Super Admin's access is computed in code, not read from the
grant table, so it works correctly even before seed.py has populated any
permission rows.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b2c3d4e5f6a7'
down_revision: Union[str, None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'admin_roles',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('slug', sa.String(length=100), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('is_system', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_admin_roles_slug'), 'admin_roles', ['slug'], unique=True)

    op.create_table(
        'permissions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('key', sa.String(length=100), nullable=False),
        sa.Column('module', sa.String(length=100), nullable=False),
        sa.Column('action', sa.String(length=50), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_permissions_key'), 'permissions', ['key'], unique=True)

    op.create_table(
        'admin_role_permissions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('admin_role_id', sa.Integer(), nullable=False),
        sa.Column('permission_key', sa.String(length=100), nullable=False),
        sa.ForeignKeyConstraint(['admin_role_id'], ['admin_roles.id'], ),
        sa.ForeignKeyConstraint(['permission_key'], ['permissions.key'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('admin_role_id', 'permission_key', name='uq_admin_role_permission'),
    )
    op.create_index(op.f('ix_admin_role_permissions_admin_role_id'), 'admin_role_permissions', ['admin_role_id'], unique=False)
    op.create_index(op.f('ix_admin_role_permissions_permission_key'), 'admin_role_permissions', ['permission_key'], unique=False)

    op.create_table(
        'system_settings',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('institute_name', sa.String(length=200), nullable=False, server_default='Arckenites'),
        sa.Column('logo_url', sa.String(length=500), nullable=True),
        sa.Column('contact_email', sa.String(length=255), nullable=True),
        sa.Column('timezone', sa.String(length=100), nullable=False, server_default='Asia/Kolkata'),
        sa.Column('date_format', sa.String(length=20), nullable=False, server_default='DD MMM YYYY'),
        sa.Column('session_timeout_minutes', sa.Integer(), nullable=False, server_default='60'),
        sa.Column('max_login_attempts', sa.Integer(), nullable=False, server_default='5'),
        sa.Column('lockout_duration_minutes', sa.Integer(), nullable=False, server_default='15'),
        sa.Column('require_strong_passwords', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('notify_new_account', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('notify_password_reset', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('notify_security_alert', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('maintenance_mode', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_by_id', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['updated_by_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )

    op.add_column('users', sa.Column('admin_role_id', sa.Integer(), nullable=True))
    op.create_foreign_key('fk_users_admin_role_id', 'users', 'admin_roles', ['admin_role_id'], ['id'])

    op.add_column('auth_audit_log', sa.Column('module', sa.String(length=50), nullable=True))
    op.add_column('auth_audit_log', sa.Column('target', sa.String(length=200), nullable=True))
    op.add_column('auth_audit_log', sa.Column('status', sa.String(length=20), nullable=True))
    op.add_column('auth_audit_log', sa.Column('actor_role', sa.String(length=50), nullable=True))

    # Seed the 3 fixed-ID system roles.
    op.execute(
        """
        INSERT INTO admin_roles (id, name, slug, description, is_system)
        VALUES
            (1, 'Super Admin', 'super_admin', 'Full system access. Cannot be renamed, deleted, or have its permissions edited.', true),
            (2, 'Admin', 'admin', 'Normal administrative access to institute management modules.', true),
            (3, 'Support Admin', 'support_admin', 'Limited, view-only operational access.', true)
        """
    )
    # Keep the id sequence past the fixed IDs we just inserted explicitly.
    op.execute("SELECT setval(pg_get_serial_sequence('admin_roles', 'id'), 3, true)")

    # Backfill: every existing role=admin user becomes Super Admin, so the
    # system is never in a "no role assigned" broken state post-deploy.
    op.execute("UPDATE users SET admin_role_id = 1 WHERE role = 'admin'")

    # Default settings row matching current hardcoded behavior exactly, so
    # nothing changes for anyone until an admin deliberately edits Settings.
    op.execute(
        """
        INSERT INTO system_settings (id, institute_name, timezone, date_format, session_timeout_minutes,
                                      max_login_attempts, lockout_duration_minutes, require_strong_passwords,
                                      maintenance_mode)
        VALUES (1, 'Arckenites', 'Asia/Kolkata', 'DD MMM YYYY', 60, 5, 15, false, false)
        """
    )


def downgrade() -> None:
    op.drop_column('auth_audit_log', 'actor_role')
    op.drop_column('auth_audit_log', 'status')
    op.drop_column('auth_audit_log', 'target')
    op.drop_column('auth_audit_log', 'module')

    op.drop_constraint('fk_users_admin_role_id', 'users', type_='foreignkey')
    op.drop_column('users', 'admin_role_id')

    op.drop_table('system_settings')

    op.drop_index(op.f('ix_admin_role_permissions_permission_key'), table_name='admin_role_permissions')
    op.drop_index(op.f('ix_admin_role_permissions_admin_role_id'), table_name='admin_role_permissions')
    op.drop_table('admin_role_permissions')

    op.drop_index(op.f('ix_permissions_key'), table_name='permissions')
    op.drop_table('permissions')

    op.drop_index(op.f('ix_admin_roles_slug'), table_name='admin_roles')
    op.drop_table('admin_roles')
