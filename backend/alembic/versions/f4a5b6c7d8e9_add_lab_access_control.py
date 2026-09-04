"""add lab_access_overrides and lab_access_audit_log tables

Revision ID: f4a5b6c7d8e9
Revises: e3f4a5b6c7d8
Create Date: 2026-09-04 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f4a5b6c7d8e9'
down_revision: Union[str, None] = 'e3f4a5b6c7d8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Only used by this one column — let create_table's own checkfirst
    # handling create the enum type exactly once, no manual pre-creation.
    lab_access_mode_enum = sa.Enum('AUTO', 'MANUAL_UNLOCK', 'MANUAL_LOCK', name='lab_access_mode_enum')

    op.create_table(
        'lab_access_overrides',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('student_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False, unique=True),
        sa.Column('access_mode', lab_access_mode_enum, nullable=False, server_default='AUTO'),
        sa.Column('reason', sa.String(length=500), nullable=True),
        sa.Column('updated_by', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index('ix_lab_access_overrides_student_id', 'lab_access_overrides', ['student_id'], unique=True)

    op.create_table(
        'lab_access_audit_log',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('student_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('admin_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('action', sa.String(length=30), nullable=False),
        sa.Column('previous_status', sa.String(length=20), nullable=False),
        sa.Column('new_status', sa.String(length=20), nullable=False),
        sa.Column('reason', sa.String(length=500), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index('ix_lab_access_audit_log_student_id', 'lab_access_audit_log', ['student_id'])


def downgrade() -> None:
    op.drop_index('ix_lab_access_audit_log_student_id', table_name='lab_access_audit_log')
    op.drop_table('lab_access_audit_log')

    op.drop_index('ix_lab_access_overrides_student_id', table_name='lab_access_overrides')
    op.drop_table('lab_access_overrides')

    sa.Enum(name='lab_access_mode_enum').drop(op.get_bind(), checkfirst=True)
