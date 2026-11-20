"""post-merge linear head (no schema change)

Revision ID: e9c5b1a8d236
Revises: d7a3f9c2b814

Empty on purpose. `d7a3f9c2b814` is a merge revision (two parents), and Alembic cannot
step back ONE revision from a merge point ("Ambiguous walk"), which would break the
CI recovery drill (`alembic upgrade head`, `alembic downgrade -1`, `alembic upgrade head`)
and any operator running `downgrade -1`. A single-parent head keeps that drill working
without changing the workflow. Later migrations simply build on this one.

No schema or data is touched in either direction.
"""
from typing import Sequence, Union

revision: str = "e9c5b1a8d236"
down_revision: Union[str, Sequence[str], None] = "d7a3f9c2b814"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
