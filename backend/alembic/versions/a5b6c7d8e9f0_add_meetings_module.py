"""add meetings module tables and audit event types

Revision ID: a5b6c7d8e9f0
Revises: f4a5b6c7d8e9
Create Date: 2026-09-04 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a5b6c7d8e9f0'
down_revision: Union[str, None] = 'f4a5b6c7d8e9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


NEW_AUDIT_EVENTS = [
    'meeting_created', 'meeting_updated', 'meeting_cancelled', 'meeting_started',
    'meeting_ended', 'meeting_deleted', 'meeting_participant_added',
    'meeting_participant_removed', 'meeting_recording_started',
    'meeting_recording_stopped', 'meeting_recording_deleted',
]


def upgrade() -> None:
    # New AuthEventType values — each ADD VALUE must run in its own
    # auto-committed statement (Postgres can't add + use an enum value in
    # the same transaction on older server versions), so autocommit here.
    with op.get_context().autocommit_block():
        for event in NEW_AUDIT_EVENTS:
            op.execute(f"ALTER TYPE auth_event_type ADD VALUE IF NOT EXISTS '{event}'")

    meeting_status = sa.Enum('SCHEDULED', 'LIVE', 'COMPLETED', 'CANCELLED', name='meeting_status_enum')
    participant_role = sa.Enum('HOST', 'CO_HOST', 'PARTICIPANT', name='meeting_participant_role_enum')
    participant_status = sa.Enum(
        'INVITED', 'WAITING', 'ADMITTED', 'JOINED', 'LEFT', 'REMOVED', 'REJECTED',
        name='meeting_participant_status_enum',
    )
    recording_status = sa.Enum('RECORDING', 'PROCESSING', 'AVAILABLE', 'FAILED', name='meeting_recording_status_enum')

    op.create_table(
        'meetings',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('meeting_token', sa.String(length=32), nullable=False),
        sa.Column('title', sa.String(length=200), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('host_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('password_hash', sa.String(length=200), nullable=True),
        sa.Column('scheduled_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('duration_minutes', sa.Integer(), nullable=False, server_default='60'),
        sa.Column('max_participants', sa.Integer(), nullable=True),
        sa.Column('status', meeting_status, nullable=False, server_default='SCHEDULED'),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('ended_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('mic_enabled', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('camera_enabled', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('screen_share_enabled', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('chat_enabled', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('notes_enabled', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('participant_notes_enabled', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('recording_enabled', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('waiting_room_enabled', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('require_approval', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint('meeting_token', name='uq_meetings_meeting_token'),
    )
    op.create_index('ix_meetings_meeting_token', 'meetings', ['meeting_token'])
    op.create_index('ix_meetings_host_id', 'meetings', ['host_id'])

    op.create_table(
        'meeting_participants',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('meeting_id', sa.Integer(), sa.ForeignKey('meetings.id'), nullable=False),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('role', participant_role, nullable=False, server_default='PARTICIPANT'),
        sa.Column('status', participant_status, nullable=False, server_default='INVITED'),
        sa.Column('joined_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('left_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index('ix_meeting_participants_meeting_id', 'meeting_participants', ['meeting_id'])
    op.create_index('ix_meeting_participants_user_id', 'meeting_participants', ['user_id'])

    op.create_table(
        'meeting_messages',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('meeting_id', sa.Integer(), sa.ForeignKey('meetings.id'), nullable=False),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('message', sa.Text(), nullable=False),
        sa.Column('is_system', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index('ix_meeting_messages_meeting_id', 'meeting_messages', ['meeting_id'])

    op.create_table(
        'meeting_notes',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('meeting_id', sa.Integer(), sa.ForeignKey('meetings.id'), nullable=False),
        sa.Column('content', sa.Text(), nullable=False, server_default=''),
        sa.Column('action_items', sa.Text(), nullable=False, server_default='[]'),
        sa.Column('updated_by', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint('meeting_id', name='uq_meeting_notes_meeting_id'),
    )
    op.create_index('ix_meeting_notes_meeting_id', 'meeting_notes', ['meeting_id'], unique=True)

    op.create_table(
        'meeting_recordings',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('meeting_id', sa.Integer(), sa.ForeignKey('meetings.id'), nullable=False),
        sa.Column('status', recording_status, nullable=False, server_default='RECORDING'),
        sa.Column('file_path', sa.String(length=500), nullable=True),
        sa.Column('duration_seconds', sa.Integer(), nullable=True),
        sa.Column('file_size_bytes', sa.Integer(), nullable=True),
        sa.Column('started_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index('ix_meeting_recordings_meeting_id', 'meeting_recordings', ['meeting_id'])


def downgrade() -> None:
    op.drop_index('ix_meeting_recordings_meeting_id', table_name='meeting_recordings')
    op.drop_table('meeting_recordings')

    op.drop_index('ix_meeting_notes_meeting_id', table_name='meeting_notes')
    op.drop_table('meeting_notes')

    op.drop_index('ix_meeting_messages_meeting_id', table_name='meeting_messages')
    op.drop_table('meeting_messages')

    op.drop_index('ix_meeting_participants_user_id', table_name='meeting_participants')
    op.drop_index('ix_meeting_participants_meeting_id', table_name='meeting_participants')
    op.drop_table('meeting_participants')

    op.drop_index('ix_meetings_host_id', table_name='meetings')
    op.drop_index('ix_meetings_meeting_token', table_name='meetings')
    op.drop_table('meetings')

    sa.Enum(name='meeting_recording_status_enum').drop(op.get_bind(), checkfirst=True)
    sa.Enum(name='meeting_participant_status_enum').drop(op.get_bind(), checkfirst=True)
    sa.Enum(name='meeting_participant_role_enum').drop(op.get_bind(), checkfirst=True)
    sa.Enum(name='meeting_status_enum').drop(op.get_bind(), checkfirst=True)

    # Note: removing values from a Postgres enum type isn't supported by a
    # simple ALTER TYPE ... DROP VALUE — leaving the AuthEventType additions
    # in place on downgrade is intentional; they're harmless if unused.
