"""add class_sessions

Revision ID: b4e8d2c6f1a9
Revises: a7b3f9d1e6c4
Create Date: 2026-08-20 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b4e8d2c6f1a9'
down_revision: Union[str, None] = 'a7b3f9d1e6c4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'class_sessions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('batch_id', sa.Integer(), nullable=False),
        sa.Column('title', sa.String(length=200), nullable=False),
        sa.Column('session_date', sa.Date(), nullable=False),
        sa.Column('start_time', sa.Time(), nullable=True),
        sa.Column('end_time', sa.Time(), nullable=True),
        sa.Column('meeting_link', sa.String(length=500), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['batch_id'], ['batches.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_class_sessions_batch_id'), 'class_sessions', ['batch_id'], unique=False)
    op.create_index(op.f('ix_class_sessions_session_date'), 'class_sessions', ['session_date'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_class_sessions_session_date'), table_name='class_sessions')
    op.drop_index(op.f('ix_class_sessions_batch_id'), table_name='class_sessions')
    op.drop_table('class_sessions')
