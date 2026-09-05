"""add batch_id to meetings

Revision ID: b6c7d8e9f0a1
Revises: a5b6c7d8e9f0
Create Date: 2026-09-05 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b6c7d8e9f0a1'
down_revision: Union[str, None] = 'a5b6c7d8e9f0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('meetings', sa.Column('batch_id', sa.Integer(), sa.ForeignKey('batches.id'), nullable=True))
    op.create_index('ix_meetings_batch_id', 'meetings', ['batch_id'])


def downgrade() -> None:
    op.drop_index('ix_meetings_batch_id', table_name='meetings')
    op.drop_column('meetings', 'batch_id')
