"""add staff to sender_type_enum

Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-08-21 00:00:00.000000

Kept as its own migration — same reasoning as d4e5f6a7b8c9: Postgres
forbids using a value added via ALTER TYPE ... ADD VALUE in any statement
that runs in the same transaction as the ALTER TYPE. The next migration
(which renames the support_tickets ownership columns) doesn't need this
value directly, but keeping the split is consistent with the rest of this
codebase's enum-broadening migrations.
"""
from typing import Sequence, Union

from alembic import op


revision: str = 'a7b8c9d0e1f2'
down_revision: Union[str, None] = 'f6a7b8c9d0e1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE sender_type_enum ADD VALUE IF NOT EXISTS 'staff'")


def downgrade() -> None:
    # Postgres has no ALTER TYPE ... DROP VALUE — matches the existing
    # precedent in d4e5f6a7b8c9's downgrade.
    pass
