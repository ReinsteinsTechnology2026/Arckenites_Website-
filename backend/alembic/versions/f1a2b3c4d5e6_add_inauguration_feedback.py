"""add inauguration_feedback

Revision ID: f1a2b3c4d5e6
Revises: 9d2f6b8c4e31
Create Date: 2026-10-29 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f1a2b3c4d5e6'
down_revision: Union[str, None] = '9d2f6b8c4e31'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'inauguration_feedback',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=200), nullable=False),
        sa.Column('domain', sa.String(length=50), nullable=False),
        sa.Column('domain_other', sa.String(length=200), nullable=True),
        sa.Column('overall_session', sa.String(length=30), nullable=False),
        sa.Column('usefulness', sa.String(length=30), nullable=False),
        sa.Column('learned_something', sa.String(length=10), nullable=False),
        sa.Column('liked_topic', sa.String(length=30), nullable=False),
        sa.Column('liked_topic_other', sa.String(length=200), nullable=True),
        sa.Column('attend_future', sa.String(length=10), nullable=False),
        sa.Column('suggestion', sa.Text(), nullable=True),
        sa.Column('contact_preference', sa.String(length=10), nullable=False),
        sa.Column('contact_number', sa.String(length=20), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_inauguration_feedback_created_at'), 'inauguration_feedback', ['created_at'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_inauguration_feedback_created_at'), table_name='inauguration_feedback')
    op.drop_table('inauguration_feedback')
