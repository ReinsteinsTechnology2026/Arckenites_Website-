"""add batch group chat tables

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-08-21 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f6a7b8c9d0e1'
down_revision: Union[str, None] = 'e5f6a7b8c9d0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'batch_messages',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('batch_id', sa.Integer(), nullable=False),
        sa.Column('sender_id', sa.Integer(), nullable=False),
        sa.Column('message', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['batch_id'], ['batches.id'], ),
        sa.ForeignKeyConstraint(['sender_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_batch_messages_batch_id'), 'batch_messages', ['batch_id'], unique=False)

    op.create_table(
        'batch_chat_read_state',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('batch_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('last_read_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['batch_id'], ['batches.id'], ),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('batch_id', 'user_id'),
    )
    op.create_index(op.f('ix_batch_chat_read_state_batch_id'), 'batch_chat_read_state', ['batch_id'], unique=False)
    op.create_index(op.f('ix_batch_chat_read_state_user_id'), 'batch_chat_read_state', ['user_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_batch_chat_read_state_user_id'), table_name='batch_chat_read_state')
    op.drop_index(op.f('ix_batch_chat_read_state_batch_id'), table_name='batch_chat_read_state')
    op.drop_table('batch_chat_read_state')

    op.drop_index(op.f('ix_batch_messages_batch_id'), table_name='batch_messages')
    op.drop_table('batch_messages')
