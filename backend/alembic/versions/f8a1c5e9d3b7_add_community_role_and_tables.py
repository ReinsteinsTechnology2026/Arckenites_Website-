"""add community value to role_enum and auth_event_type

Revision ID: f8a1c5e9d3b7
Revises: d4e7a2c8f6b1
Create Date: 2026-09-24 00:00:00.000000

Kept as its own migration, separate from the community tables that follow
it — same reasoning as a1b2c3d4e5f6/d4e5f6a7b8c9: Postgres forbids using a
value added via ALTER TYPE ... ADD VALUE in any statement running in the
same transaction as the ALTER TYPE, and this project's Alembic setup wraps
each migration's upgrade() in one transaction.
"""
from typing import Sequence, Union

from alembic import op


revision: str = 'f8a1c5e9d3b7'
down_revision: Union[str, None] = 'd4e7a2c8f6b1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

NEW_AUTH_EVENT_VALUES = [
    "community_registration_started",
    "community_otp_sent",
    "community_otp_verified",
    "community_otp_failed",
    "community_otp_resent",
    "community_account_created",
    "community_member_enabled",
    "community_member_disabled",
]


def upgrade() -> None:
    op.execute("ALTER TYPE role_enum ADD VALUE IF NOT EXISTS 'community'")
    for value in NEW_AUTH_EVENT_VALUES:
        op.execute(f"ALTER TYPE auth_event_type ADD VALUE IF NOT EXISTS '{value}'")


def downgrade() -> None:
    # Postgres has no ALTER TYPE ... DROP VALUE in any version — added enum
    # values are left in place on downgrade, same as every prior enum
    # broadening migration in this project.
    pass
