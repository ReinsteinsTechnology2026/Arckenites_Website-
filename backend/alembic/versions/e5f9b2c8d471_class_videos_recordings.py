"""let Class Videos hold published recordings as well as posted links

Revision ID: e5f9b2c8d471
Revises: d4e8a1b7c356

Additive and backward compatible: existing rows keep their URL and become
source='LINK'. `video_url` becomes nullable ONLY so a recording entry (which
deliberately has no URL) can exist; a CHECK still requires either a URL or a
recording. `recording_id` is unique so a retried publish can never duplicate a
recording's entry.

Downgrade deletes the recording entries (they cannot satisfy NOT NULL
video_url) - the recordings themselves in class_recordings are untouched.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "e5f9b2c8d471"
down_revision: Union[str, None] = "d4e8a1b7c356"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("class_videos") as batch:
        batch.alter_column("video_url", existing_type=sa.String(500), nullable=True)
        batch.add_column(sa.Column("source", sa.String(16), nullable=False, server_default="LINK"))
        batch.add_column(sa.Column("recording_id", sa.Uuid(), nullable=True))
        batch.create_foreign_key("fk_class_videos_recording_id", "class_recordings", ["recording_id"], ["id"])
        batch.create_unique_constraint("uq_class_videos_recording_id", ["recording_id"])
        batch.create_check_constraint("ck_class_videos_has_target", "video_url IS NOT NULL OR recording_id IS NOT NULL")
        batch.create_check_constraint("ck_class_videos_source", "source IN ('LINK', 'RECORDING')")


def downgrade() -> None:
    op.execute("DELETE FROM class_videos WHERE recording_id IS NOT NULL")
    with op.batch_alter_table("class_videos") as batch:
        batch.drop_constraint("ck_class_videos_source", type_="check")
        batch.drop_constraint("ck_class_videos_has_target", type_="check")
        batch.drop_constraint("uq_class_videos_recording_id", type_="unique")
        batch.drop_constraint("fk_class_videos_recording_id", type_="foreignkey")
        batch.drop_column("recording_id")
        batch.drop_column("source")
        batch.alter_column("video_url", existing_type=sa.String(500), nullable=False)
