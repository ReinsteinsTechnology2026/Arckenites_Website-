"""add address to student_profiles and staff_profiles

Revision ID: d2e3f4a5b6c7
Revises: c1d2e3f4a5b6
Create Date: 2026-08-25 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd2e3f4a5b6c7'
down_revision: Union[str, None] = 'c1d2e3f4a5b6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('student_profiles', sa.Column('address', sa.String(length=500), nullable=True))
    op.add_column('staff_profiles', sa.Column('address', sa.String(length=500), nullable=True))


def downgrade() -> None:
    op.drop_column('staff_profiles', 'address')
    op.drop_column('student_profiles', 'address')
