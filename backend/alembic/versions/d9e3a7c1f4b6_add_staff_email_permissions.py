"""add staff email and permissions

Revision ID: d9e3a7c1f4b6
Revises: c4d8f1a5b9e2
Create Date: 2026-08-14 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd9e3a7c1f4b6'
down_revision: Union[str, None] = 'c4d8f1a5b9e2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('staff_profiles', sa.Column('email', sa.String(length=255), nullable=True))
    op.add_column('staff_profiles', sa.Column('permissions', sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column('staff_profiles', 'permissions')
    op.drop_column('staff_profiles', 'email')
