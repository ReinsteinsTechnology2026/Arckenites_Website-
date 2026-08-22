"""add contact_enquiries

Revision ID: e5c8b3d9a2f4
Revises: c9f2a4e7b3d8
Create Date: 2026-08-20 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e5c8b3d9a2f4'
down_revision: Union[str, None] = 'c9f2a4e7b3d8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'contact_enquiries',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('full_name', sa.String(length=200), nullable=False),
        sa.Column('phone', sa.String(length=30), nullable=False),
        sa.Column('email', sa.String(length=255), nullable=False),
        sa.Column('subject', sa.String(length=200), nullable=True),
        sa.Column('message', sa.Text(), nullable=False),
        sa.Column('is_read', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_contact_enquiries_created_at'), 'contact_enquiries', ['created_at'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_contact_enquiries_created_at'), table_name='contact_enquiries')
    op.drop_table('contact_enquiries')
