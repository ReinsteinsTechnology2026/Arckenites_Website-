"""retire recording management permissions; general meetings are never recorded

Revision ID: b8d3f6a2c915
Revises: e5f9b2c8d471

Follows the class videos/recordings revision (e5f9b2c8d471). It was originally a merge
with the separate feedback-sheets line; that line is no longer part of this release, so
this revision has the single parent that actually carries its prerequisites.

Approved recording rules:
  * Only the class trainer controls a recording. No administrator (not even a
    Super Admin) can approve, start, stop, play, download, revoke or delete one.
  * General meetings are never recorded.

So this migration
  1. removes any role grants of `recordings.manage` and `recordings.delete`,
     then the permission rows themselves (`recordings.view`, the read-only
     status/metadata permission, is kept);
  2. clears the legacy `meetings.recording_enabled` flag.

It deletes NO recording data: `class_recordings`, `meeting_recordings`,
`recording_events`, their files and every audit log row are untouched.

The downgrade does not restore the removed grants (a retired permission is
not something to re-issue automatically) and does not re-enable the flag.

NOT applied anywhere by this change. It is meant to run through the normal
reviewed deployment, after a verified database backup.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "b8d3f6a2c915"
down_revision: Union[str, Sequence[str], None] = "e5f9b2c8d471"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

RETIRED_PERMISSIONS = ("recordings.manage", "recordings.delete")


def upgrade() -> None:
    bind = op.get_bind()
    for key in RETIRED_PERMISSIONS:
        # grants reference permissions.key, so they go first
        bind.execute(sa.text("DELETE FROM admin_role_permissions WHERE permission_key = :key"), {"key": key})
        bind.execute(sa.text("DELETE FROM permissions WHERE key = :key"), {"key": key})
    bind.execute(
        sa.text("UPDATE meetings SET recording_enabled = :off WHERE recording_enabled = :on"),
        {"off": False, "on": True},
    )


def downgrade() -> None:
    # Intentionally a no-op beyond un-merging the heads: retired grants are
    # never re-issued automatically.
    pass
