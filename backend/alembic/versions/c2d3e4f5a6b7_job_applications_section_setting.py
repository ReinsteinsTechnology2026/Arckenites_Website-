"""Persist the student Job Applications section switch.

Revision ID: c2d3e4f5a6b7
Revises: b1c2d3e4f5a6
"""
from alembic import op
import sqlalchemy as sa

revision = "c2d3e4f5a6b7"
down_revision = "b1c2d3e4f5a6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Preserve the existing student experience until an authorized admin
    # deliberately switches the section off.
    op.add_column("system_settings", sa.Column(
        "job_applications_section_enabled", sa.Boolean(),
        nullable=False, server_default=sa.true(),
    ))


def downgrade() -> None:
    op.drop_column("system_settings", "job_applications_section_enabled")
