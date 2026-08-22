"""broaden auth_event_type enum for full activity logging

Revision ID: a1b2c3d4e5f6
Revises: e5c8b3d9a2f4
Create Date: 2026-08-20 00:00:00.000000

Kept as its own migration: Postgres forbids using a value added via
ALTER TYPE ... ADD VALUE in any statement that runs in the same transaction
as the ALTER TYPE, and this project's Alembic setup wraps each migration's
upgrade() in one transaction. Splitting the enum broadening into its own
revision (with the new tables/seed data that reference these values in the
next revision) sidesteps that restriction entirely.
"""
from typing import Sequence, Union

from alembic import op


revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = 'e5c8b3d9a2f4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

NEW_VALUES = [
    "session_revoked",
    "student_created", "student_updated", "student_enabled", "student_disabled",
    "student_deleted", "student_password_reset",
    "trainer_created", "trainer_updated", "trainer_enabled", "trainer_disabled",
    "trainer_deleted", "trainer_password_reset", "trainer_permissions_changed",
    "program_created", "program_updated", "program_deleted",
    "batch_created", "batch_updated", "batch_deleted",
    "admin_user_created", "admin_user_updated", "admin_user_enabled", "admin_user_disabled",
    "admin_user_deleted", "admin_user_password_reset", "admin_user_role_changed",
    "role_created", "role_updated", "role_deleted", "permission_changed",
    "settings_changed",
]


def upgrade() -> None:
    for value in NEW_VALUES:
        op.execute(f"ALTER TYPE auth_event_type ADD VALUE IF NOT EXISTS '{value}'")


def downgrade() -> None:
    # Postgres has no ALTER TYPE ... DROP VALUE in any version — the added
    # enum values are left in place on downgrade (unused, harmless) rather
    # than attempting a full enum-recreate/column-swap dance.
    pass
