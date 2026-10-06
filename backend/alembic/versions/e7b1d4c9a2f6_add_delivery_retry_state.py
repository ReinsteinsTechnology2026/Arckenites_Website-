"""add attempts and next_attempt_at to community post email deliveries

Retry state for transient Microsoft Graph failures (429 / 503 / gateway
errors). A row stays 'pending' while it is waiting to be retried, so the
attempts counter and the earliest retry time must be stored with it.

Purely additive: nullable time column, and an integer column with a
server default of 0. No existing row is changed in a way that affects
delivery, and downgrading simply drops the two columns.

Revision ID: e7b1d4c9a2f6
Revises: d5a7c3e1f9b4
Create Date: 2026-10-06 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e7b1d4c9a2f6'
down_revision: Union[str, None] = 'd5a7c3e1f9b4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'community_post_email_deliveries',
        sa.Column('attempts', sa.Integer(), nullable=False, server_default='0'),
    )
    op.add_column(
        'community_post_email_deliveries',
        sa.Column('next_attempt_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        'ix_community_post_deliveries_due',
        'community_post_email_deliveries',
        ['status', 'next_attempt_at'],
    )


def downgrade() -> None:
    op.drop_index('ix_community_post_deliveries_due', table_name='community_post_email_deliveries')
    op.drop_column('community_post_email_deliveries', 'next_attempt_at')
    op.drop_column('community_post_email_deliveries', 'attempts')
