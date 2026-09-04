"""add lab_slot_bookings table

Revision ID: e3f4a5b6c7d8
Revises: d2e3f4a5b6c7
Create Date: 2026-09-04 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e3f4a5b6c7d8'
down_revision: Union[str, None] = 'd2e3f4a5b6c7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'lab_slot_bookings',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('student_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('slot_date', sa.Date(), nullable=False),
        sa.Column('start_time', sa.Time(), nullable=False),
        sa.Column('end_time', sa.Time(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint('student_id', 'slot_date', 'start_time', name='uq_lab_booking_student_slot'),
    )
    op.create_index('ix_lab_slot_bookings_student_id', 'lab_slot_bookings', ['student_id'])
    op.create_index('ix_lab_slot_bookings_slot_date', 'lab_slot_bookings', ['slot_date'])


def downgrade() -> None:
    op.drop_index('ix_lab_slot_bookings_slot_date', table_name='lab_slot_bookings')
    op.drop_index('ix_lab_slot_bookings_student_id', table_name='lab_slot_bookings')
    op.drop_table('lab_slot_bookings')
