"""add student program

Revision ID: a3f9c2d4e1b7
Revises: d17b417f440b
Create Date: 2026-08-14 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a3f9c2d4e1b7'
down_revision: Union[str, None] = 'd17b417f440b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    program_enum = sa.Enum(
        'official_certification',
        'corporate_training',
        'institutional',
        'placement_training',
        'trainers_program',
        'internship',
        name='program_enum',
    )
    program_enum.create(op.get_bind(), checkfirst=True)
    op.add_column('student_profiles', sa.Column('program', program_enum, nullable=True))


def downgrade() -> None:
    op.drop_column('student_profiles', 'program')
    sa.Enum(name='program_enum').drop(op.get_bind(), checkfirst=True)
