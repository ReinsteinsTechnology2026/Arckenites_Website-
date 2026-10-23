"""broaden auth_event_type for trainer scheduling

Revision ID: 9c1e5a7b3d20
Revises: c365dd2994ef

Own migration, same reasoning as a1b2c3d4e5f6 and d4e5f6a7b8c9: Postgres
forbids using a value added via ALTER TYPE ... ADD VALUE in the transaction
that adds it. The next migration (tables + permissions) may use them.
"""
from typing import Sequence, Union

from alembic import op


revision: str = "9c1e5a7b3d20"
down_revision: Union[str, None] = "c365dd2994ef"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

NEW_VALUES = [
    "trainer_availability_changed",
    "batch_trainer_assigned",
    "batch_trainer_changed",
    "batch_trainer_unassigned",
    "batch_trainer_override",
    "trainer_expertise_changed",
]


def upgrade() -> None:
    for value in NEW_VALUES:
        op.execute(f"ALTER TYPE auth_event_type ADD VALUE IF NOT EXISTS '{value}'")


def downgrade() -> None:
    # Postgres cannot drop enum values, so they stay in the type. Rows already
    # written with them would make the previous application raise LookupError when
    # it loads the audit log, so re-label those rows with the closest pre-existing
    # value (the detail/module/target columns keep the real meaning).
    op.execute("UPDATE auth_audit_log SET event_type = 'batch_updated' "
               "WHERE event_type IN ('batch_trainer_assigned', 'batch_trainer_changed', "
               "'batch_trainer_unassigned', 'batch_trainer_override')")
    op.execute("UPDATE auth_audit_log SET event_type = 'trainer_updated' "
               "WHERE event_type IN ('trainer_availability_changed', 'trainer_expertise_changed')")
