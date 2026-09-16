"""add leads table

Revision ID: 7f9c63c4ea71
Revises: d8e9f0a1b2c3
Create Date: 2026-09-16 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '7f9c63c4ea71'
down_revision: Union[str, None] = 'd8e9f0a1b2c3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    lead_status_enum = sa.Enum('new', 'contacted', 'qualified', 'converted', 'lost', name='lead_status_enum')

    op.create_table(
        'leads',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('full_name', sa.String(length=200), nullable=False),
        sa.Column('phone', sa.String(length=30), nullable=True),
        sa.Column('email', sa.String(length=255), nullable=True),
        sa.Column('source', sa.String(length=100), nullable=True),
        sa.Column('status', lead_status_enum, nullable=False),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_leads_status'), 'leads', ['status'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_leads_status'), table_name='leads')
    op.drop_table('leads')
    sa.Enum(name='lead_status_enum').drop(op.get_bind(), checkfirst=True)
