"""add batches and batch_enrollments

Revision ID: f3a7c1e9b2d5
Revises: e2f6a9c3d8b1
Create Date: 2026-08-19 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f3a7c1e9b2d5'
down_revision: Union[str, None] = 'e2f6a9c3d8b1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Unlike op.add_column (used for program_enum in an earlier migration),
    # op.create_table() creates any new Enum type referenced by its columns
    # on its own — no separate .create() call needed or wanted here.
    batch_type_enum = sa.Enum('online', 'offline', 'hybrid', name='batch_type_enum')
    batch_status_enum = sa.Enum('upcoming', 'active', 'paused', 'completed', 'cancelled', name='batch_status_enum')

    op.create_table(
        'batches',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=200), nullable=False),
        sa.Column('course', sa.String(length=200), nullable=True),
        sa.Column('batch_type', batch_type_enum, nullable=False),
        sa.Column('status', batch_status_enum, nullable=False),
        sa.Column('trainer_id', sa.Integer(), nullable=True),
        sa.Column('start_date', sa.Date(), nullable=True),
        sa.Column('end_date', sa.Date(), nullable=True),
        sa.Column('max_capacity', sa.Integer(), nullable=True),
        sa.Column('class_days', sa.String(length=100), nullable=True),
        sa.Column('start_time', sa.Time(), nullable=True),
        sa.Column('end_time', sa.Time(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['trainer_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )

    op.create_table(
        'batch_enrollments',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('batch_id', sa.Integer(), nullable=False),
        sa.Column('student_id', sa.Integer(), nullable=False),
        sa.Column('joined_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['batch_id'], ['batches.id'], ),
        sa.ForeignKeyConstraint(['student_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('batch_id', 'student_id'),
    )
    op.create_index(op.f('ix_batch_enrollments_batch_id'), 'batch_enrollments', ['batch_id'], unique=False)
    op.create_index(op.f('ix_batch_enrollments_student_id'), 'batch_enrollments', ['student_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_batch_enrollments_student_id'), table_name='batch_enrollments')
    op.drop_index(op.f('ix_batch_enrollments_batch_id'), table_name='batch_enrollments')
    op.drop_table('batch_enrollments')
    op.drop_table('batches')
    sa.Enum(name='batch_status_enum').drop(op.get_bind(), checkfirst=True)
    sa.Enum(name='batch_type_enum').drop(op.get_bind(), checkfirst=True)
