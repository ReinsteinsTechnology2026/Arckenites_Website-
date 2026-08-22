"""rename support_tickets ownership columns to requester

Generalizes the ticket owner from "the student" to "the requester" so
trainers can also raise support tickets, without a parallel staff_id
column — one ticket table now serves both roles; ticket.requester.role
tells you which.

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-08-21 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


revision: str = 'b8c9d0e1f2a3'
down_revision: Union[str, None] = 'a7b8c9d0e1f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column('support_tickets', 'student_id', new_column_name='requester_id')
    op.alter_column('support_tickets', 'student_last_read_at', new_column_name='requester_last_read_at')
    op.execute("ALTER INDEX ix_support_tickets_student_id RENAME TO ix_support_tickets_requester_id")
    op.execute(
        "ALTER TABLE support_tickets RENAME CONSTRAINT support_tickets_student_id_fkey "
        "TO support_tickets_requester_id_fkey"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE support_tickets RENAME CONSTRAINT support_tickets_requester_id_fkey "
        "TO support_tickets_student_id_fkey"
    )
    op.execute("ALTER INDEX ix_support_tickets_requester_id RENAME TO ix_support_tickets_student_id")
    op.alter_column('support_tickets', 'requester_last_read_at', new_column_name='student_last_read_at')
    op.alter_column('support_tickets', 'requester_id', new_column_name='student_id')
