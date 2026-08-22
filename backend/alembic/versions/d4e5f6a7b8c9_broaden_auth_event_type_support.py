"""broaden auth_event_type for support tickets

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-08-21 00:00:00.000000

Kept as its own migration — same reasoning as a1b2c3d4e5f6: Postgres
forbids using a value added via ALTER TYPE ... ADD VALUE in any statement
that runs in the same transaction as the ALTER TYPE. The next migration
(which creates the support tables) is free to use these values.
"""
from typing import Sequence, Union

from alembic import op


revision: str = 'd4e5f6a7b8c9'
down_revision: Union[str, None] = 'c3d4e5f6a7b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

NEW_VALUES = [
    'support_ticket_created',
    'support_ticket_replied',
    'support_ticket_assigned',
    'support_ticket_status_changed',
    'support_ticket_priority_changed',
    'support_ticket_internal_note_added',
    'support_ticket_closed',
    'support_ticket_reopened',
    'support_ticket_deleted',
]


def upgrade() -> None:
    for value in NEW_VALUES:
        op.execute(f"ALTER TYPE auth_event_type ADD VALUE IF NOT EXISTS '{value}'")


def downgrade() -> None:
    # Postgres has no ALTER TYPE ... DROP VALUE, ever, in any version.
    # Accepted limitation, consistent with the previous auth_event_type
    # broadening migration — the values simply sit unused if downgraded.
    pass
