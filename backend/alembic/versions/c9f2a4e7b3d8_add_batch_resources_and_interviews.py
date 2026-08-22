"""add lab_access, class_videos, study_materials, interview_schedules

Revision ID: c9f2a4e7b3d8
Revises: b4e8d2c6f1a9
Create Date: 2026-08-20 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c9f2a4e7b3d8'
down_revision: Union[str, None] = 'b4e8d2c6f1a9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'lab_access',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('batch_id', sa.Integer(), nullable=False),
        sa.Column('title', sa.String(length=200), nullable=False),
        sa.Column('access_url', sa.String(length=500), nullable=False),
        sa.Column('username', sa.String(length=200), nullable=True),
        sa.Column('password', sa.String(length=200), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['batch_id'], ['batches.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_lab_access_batch_id'), 'lab_access', ['batch_id'], unique=False)

    op.create_table(
        'class_videos',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('batch_id', sa.Integer(), nullable=False),
        sa.Column('title', sa.String(length=200), nullable=False),
        sa.Column('video_url', sa.String(length=500), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['batch_id'], ['batches.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_class_videos_batch_id'), 'class_videos', ['batch_id'], unique=False)

    op.create_table(
        'study_materials',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('batch_id', sa.Integer(), nullable=False),
        sa.Column('title', sa.String(length=200), nullable=False),
        sa.Column('file_url', sa.String(length=500), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['batch_id'], ['batches.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_study_materials_batch_id'), 'study_materials', ['batch_id'], unique=False)

    interview_mode_enum = sa.Enum('online', 'offline', name='interview_mode_enum')
    interview_status_enum = sa.Enum('scheduled', 'completed', 'cancelled', name='interview_status_enum')

    op.create_table(
        'interview_schedules',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('student_id', sa.Integer(), nullable=False),
        sa.Column('company_name', sa.String(length=200), nullable=False),
        sa.Column('role', sa.String(length=200), nullable=True),
        sa.Column('interview_date', sa.Date(), nullable=False),
        sa.Column('interview_time', sa.Time(), nullable=True),
        sa.Column('mode', interview_mode_enum, nullable=False),
        sa.Column('location_or_link', sa.String(length=500), nullable=True),
        sa.Column('status', interview_status_enum, nullable=False),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['student_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_interview_schedules_student_id'), 'interview_schedules', ['student_id'], unique=False)
    op.create_index(op.f('ix_interview_schedules_interview_date'), 'interview_schedules', ['interview_date'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_interview_schedules_interview_date'), table_name='interview_schedules')
    op.drop_index(op.f('ix_interview_schedules_student_id'), table_name='interview_schedules')
    op.drop_table('interview_schedules')
    sa.Enum(name='interview_status_enum').drop(op.get_bind(), checkfirst=True)
    sa.Enum(name='interview_mode_enum').drop(op.get_bind(), checkfirst=True)

    op.drop_index(op.f('ix_study_materials_batch_id'), table_name='study_materials')
    op.drop_table('study_materials')

    op.drop_index(op.f('ix_class_videos_batch_id'), table_name='class_videos')
    op.drop_table('class_videos')

    op.drop_index(op.f('ix_lab_access_batch_id'), table_name='lab_access')
    op.drop_table('lab_access')
