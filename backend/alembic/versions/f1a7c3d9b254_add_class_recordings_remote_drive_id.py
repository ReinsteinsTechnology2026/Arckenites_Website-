"""add class_recordings.remote_drive_id

Revision ID: f1a7c3d9b254
Revises: e9c5b1a8d236

Records which OneDrive drive holds each recording, so playback, integrity
re-checks and retention keep working if ONEDRIVE_DRIVE_ID is ever changed.

Additive and nullable: no default, no table rewrite, no backfill. Existing rows
stay NULL and keep using the configured drive, exactly as before. Old code
ignores the column, so deploys and rollbacks in either order are safe.
Downgrade drops only this column.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f1a7c3d9b254"
down_revision: Union[str, Sequence[str], None] = "e9c5b1a8d236"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("class_recordings", sa.Column("remote_drive_id", sa.String(length=300), nullable=True))


def downgrade() -> None:
    op.drop_column("class_recordings", "remote_drive_id")
