"""add support ticket tables

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-08-21 00:00:01.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e5f6a7b8c9d0'
down_revision: Union[str, None] = 'd4e5f6a7b8c9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    ticket_status_enum = sa.Enum(
        'open', 'in_progress', 'waiting_for_student', 'resolved', 'closed', name='ticket_status_enum',
    )
    ticket_priority_enum = sa.Enum('low', 'medium', 'high', name='ticket_priority_enum')
    ticket_category_enum = sa.Enum(
        'account_login', 'course_access', 'batch', 'classes_schedule', 'assignments', 'assessments',
        'certificates', 'payment_fees', 'technical_issue', 'trainer_training', 'other',
        name='ticket_category_enum',
    )
    sender_type_enum = sa.Enum('student', 'admin', name='sender_type_enum')

    op.create_table(
        'support_tickets',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('ticket_number', sa.String(length=20), nullable=False),
        sa.Column('student_id', sa.Integer(), nullable=False),
        sa.Column('subject', sa.String(length=200), nullable=False),
        sa.Column('category', ticket_category_enum, nullable=False),
        sa.Column('priority', ticket_priority_enum, nullable=False),
        sa.Column('status', ticket_status_enum, nullable=False),
        sa.Column('assigned_to', sa.Integer(), nullable=True),
        sa.Column('student_last_read_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('admin_last_read_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('closed_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['student_id'], ['users.id'], ),
        sa.ForeignKeyConstraint(['assigned_to'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('ticket_number'),
    )
    op.create_index(op.f('ix_support_tickets_ticket_number'), 'support_tickets', ['ticket_number'], unique=True)
    op.create_index(op.f('ix_support_tickets_student_id'), 'support_tickets', ['student_id'], unique=False)
    op.create_index(op.f('ix_support_tickets_status'), 'support_tickets', ['status'], unique=False)

    op.create_table(
        'support_messages',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('ticket_id', sa.Integer(), nullable=False),
        sa.Column('sender_id', sa.Integer(), nullable=False),
        sa.Column('sender_type', sender_type_enum, nullable=False),
        sa.Column('message', sa.Text(), nullable=False),
        sa.Column('is_internal', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['ticket_id'], ['support_tickets.id'], ),
        sa.ForeignKeyConstraint(['sender_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_support_messages_ticket_id'), 'support_messages', ['ticket_id'], unique=False)

    op.create_table(
        'support_attachments',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('ticket_id', sa.Integer(), nullable=False),
        sa.Column('message_id', sa.Integer(), nullable=False),
        sa.Column('file_name', sa.String(length=255), nullable=False),
        sa.Column('file_type', sa.String(length=100), nullable=False),
        sa.Column('file_size', sa.Integer(), nullable=False),
        sa.Column('storage_reference', sa.String(length=255), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['ticket_id'], ['support_tickets.id'], ),
        sa.ForeignKeyConstraint(['message_id'], ['support_messages.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_support_attachments_ticket_id'), 'support_attachments', ['ticket_id'], unique=False)
    op.create_index(op.f('ix_support_attachments_message_id'), 'support_attachments', ['message_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_support_attachments_message_id'), table_name='support_attachments')
    op.drop_index(op.f('ix_support_attachments_ticket_id'), table_name='support_attachments')
    op.drop_table('support_attachments')

    op.drop_index(op.f('ix_support_messages_ticket_id'), table_name='support_messages')
    op.drop_table('support_messages')

    op.drop_index(op.f('ix_support_tickets_status'), table_name='support_tickets')
    op.drop_index(op.f('ix_support_tickets_student_id'), table_name='support_tickets')
    op.drop_index(op.f('ix_support_tickets_ticket_number'), table_name='support_tickets')
    op.drop_table('support_tickets')

    sa.Enum(name='sender_type_enum').drop(op.get_bind(), checkfirst=True)
    sa.Enum(name='ticket_category_enum').drop(op.get_bind(), checkfirst=True)
    sa.Enum(name='ticket_priority_enum').drop(op.get_bind(), checkfirst=True)
    sa.Enum(name='ticket_status_enum').drop(op.get_bind(), checkfirst=True)
