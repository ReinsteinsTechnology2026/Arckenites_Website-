"""add class_private_notes (student private notes per class occurrence)

Development migration for Phase 2 milestone 3. Additive only: one new table,
nothing existing is altered or deleted. NOT applied to any real database by
the test suite.

Revision ID: c4e8a1d7f3b6
Revises: b8d3f6a2c915
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c4e8a1d7f3b6"
down_revision: Union[str, Sequence[str], None] = "b8d3f6a2c915"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "class_private_notes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("class_session_runs.id"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("content", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("run_id", "user_id", name="uq_class_private_notes_run_user"),
    )
    op.create_index("ix_class_private_notes_run_id", "class_private_notes", ["run_id"])
    op.create_index("ix_class_private_notes_user_id", "class_private_notes", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_class_private_notes_user_id", table_name="class_private_notes")
    op.drop_index("ix_class_private_notes_run_id", table_name="class_private_notes")
    op.drop_table("class_private_notes")
