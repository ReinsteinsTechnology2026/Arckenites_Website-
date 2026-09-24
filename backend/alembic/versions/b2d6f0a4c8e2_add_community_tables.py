"""add community_registrations and community_profiles tables

Revision ID: b2d6f0a4c8e2
Revises: f8a1c5e9d3b7
Create Date: 2026-09-24 00:00:00.000001

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b2d6f0a4c8e2'
down_revision: Union[str, None] = 'f8a1c5e9d3b7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    community_registration_status_enum = sa.Enum('pending', 'verified', name='community_registration_status_enum')

    op.create_table(
        'community_registrations',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('full_name', sa.String(length=200), nullable=False),
        sa.Column('mobile_number', sa.String(length=30), nullable=False),
        sa.Column('email', sa.String(length=255), nullable=False),
        sa.Column('status', community_registration_status_enum, nullable=False, server_default='pending'),
        sa.Column('otp_hash', sa.String(length=255), nullable=True),
        sa.Column('otp_expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('otp_attempts', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('otp_send_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('otp_window_started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('verified_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('verification_token_hash', sa.String(length=255), nullable=True),
        sa.Column('verification_token_expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index('ix_community_registrations_email', 'community_registrations', ['email'], unique=True)

    op.create_table(
        'community_profiles',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False, unique=True),
        sa.Column('mobile_number', sa.String(length=30), nullable=False),
        sa.Column('email', sa.String(length=255), nullable=False),
        sa.Column('email_verified_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table('community_profiles')
    op.drop_index('ix_community_registrations_email', table_name='community_registrations')
    op.drop_table('community_registrations')
    sa.Enum(name='community_registration_status_enum').drop(op.get_bind(), checkfirst=True)
