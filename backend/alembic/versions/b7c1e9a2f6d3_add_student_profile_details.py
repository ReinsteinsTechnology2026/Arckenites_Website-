"""add student profile details (email, current_role)

Revision ID: b7c1e9a2f6d3
Revises: a3f9c2d4e1b7
Create Date: 2026-08-14 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b7c1e9a2f6d3'
down_revision: Union[str, None] = 'a3f9c2d4e1b7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    current_role_enum = sa.Enum('student', 'employer', name='current_role_enum')
    current_role_enum.create(op.get_bind(), checkfirst=True)

    op.add_column('student_profiles', sa.Column('email', sa.String(length=255), nullable=True))
    op.add_column('student_profiles', sa.Column('current_role', current_role_enum, nullable=True))


def downgrade() -> None:
    op.drop_column('student_profiles', 'current_role')
    op.drop_column('student_profiles', 'email')
    sa.Enum(name='current_role_enum').drop(op.get_bind(), checkfirst=True)
