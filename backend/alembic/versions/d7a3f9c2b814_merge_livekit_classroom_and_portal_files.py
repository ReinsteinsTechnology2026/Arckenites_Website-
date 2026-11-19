"""merge the LiveKit classroom/recording line with production portal file storage

Revision ID: d7a3f9c2b814
Revises: c4e8a1d7f3b6, c6d8a2f4e107

Merge revision only. It performs NO schema or data change.

Two additive lines of migrations grew from production revision 9d2f6b8c4e31:

  * c6d8a2f4e107  portal_files (private portal file storage; already on main)
  * d4e8a1b7c356 -> e5f9b2c8d471 -> b8d3f6a2c915 -> c4e8a1d7f3b6  class runs and recordings,
    class videos, recording-permission cleanup and class private notes

Joining them gives `alembic upgrade head` a single target. A database already at
c6d8a2f4e107 (production) upgrades cleanly through the LiveKit line to this revision.

The downgrade only un-merges the heads; it changes nothing in the database.
"""
from typing import Sequence, Union

revision: str = "d7a3f9c2b814"
down_revision: Union[str, Sequence[str], None] = ("c4e8a1d7f3b6", "c6d8a2f4e107")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
