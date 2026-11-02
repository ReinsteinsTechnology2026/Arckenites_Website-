"""track Google Sheets sync for inauguration_feedback

Revision ID: a7c3d9e2f1b8
Revises: f1a2b3c4d5e6
Create Date: 2026-10-31 00:00:00.000000

"""
import uuid
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a7c3d9e2f1b8'
down_revision: Union[str, None] = 'f1a2b3c4d5e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('inauguration_feedback', sa.Column('submission_id', sa.String(length=36), nullable=True))
    op.add_column('inauguration_feedback', sa.Column('sheets_sync_status', sa.String(length=10), server_default='PENDING', nullable=False))
    op.add_column('inauguration_feedback', sa.Column('sheets_sync_attempts', sa.Integer(), server_default='0', nullable=False))
    op.add_column('inauguration_feedback', sa.Column('sheets_next_attempt_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('inauguration_feedback', sa.Column('sheets_synced_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('inauguration_feedback', sa.Column('sheets_last_error', sa.String(length=100), nullable=True))

    # Rows saved before this migration get an id too, so they can be mirrored to the sheet.
    bind = op.get_bind()
    for (row_id,) in bind.execute(sa.text('SELECT id FROM inauguration_feedback')).fetchall():
        bind.execute(
            sa.text('UPDATE inauguration_feedback SET submission_id = :sid WHERE id = :id'),
            {'sid': str(uuid.uuid4()), 'id': row_id},
        )

    op.alter_column('inauguration_feedback', 'submission_id', existing_type=sa.String(length=36), nullable=False)
    op.create_index(op.f('ix_inauguration_feedback_submission_id'), 'inauguration_feedback', ['submission_id'], unique=True)
    op.create_index(op.f('ix_inauguration_feedback_sheets_sync_status'), 'inauguration_feedback', ['sheets_sync_status'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_inauguration_feedback_sheets_sync_status'), table_name='inauguration_feedback')
    op.drop_index(op.f('ix_inauguration_feedback_submission_id'), table_name='inauguration_feedback')
    op.drop_column('inauguration_feedback', 'sheets_last_error')
    op.drop_column('inauguration_feedback', 'sheets_synced_at')
    op.drop_column('inauguration_feedback', 'sheets_next_attempt_at')
    op.drop_column('inauguration_feedback', 'sheets_sync_attempts')
    op.drop_column('inauguration_feedback', 'sheets_sync_status')
    op.drop_column('inauguration_feedback', 'submission_id')
