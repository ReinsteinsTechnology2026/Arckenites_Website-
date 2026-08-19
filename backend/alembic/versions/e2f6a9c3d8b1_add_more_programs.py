"""add interview_crack, job_assist, college_projects to program_enum

Revision ID: e2f6a9c3d8b1
Revises: d9e3a7c1f4b6
Create Date: 2026-08-18 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


revision: str = 'e2f6a9c3d8b1'
down_revision: Union[str, None] = 'd9e3a7c1f4b6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Postgres enums can't drop values, so downgrade is intentionally a no-op —
    # matches the ADD VALUE-only pattern used anywhere else this enum grows.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE program_enum ADD VALUE IF NOT EXISTS 'interview_crack'")
        op.execute("ALTER TYPE program_enum ADD VALUE IF NOT EXISTS 'job_assist'")
        op.execute("ALTER TYPE program_enum ADD VALUE IF NOT EXISTS 'college_projects'")


def downgrade() -> None:
    pass
