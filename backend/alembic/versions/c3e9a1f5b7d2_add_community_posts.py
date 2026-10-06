"""add community_posts and community_post_email_deliveries tables

Revision ID: c3e9a1f5b7d2
Revises: b2d6f0a4c8e2
Create Date: 2026-10-05 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c3e9a1f5b7d2'
down_revision: Union[str, None] = 'b2d6f0a4c8e2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'community_posts',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('kind', sa.Enum('announcement', 'update', name='community_post_kind_enum'), nullable=False),
        sa.Column('title', sa.String(length=200), nullable=False),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('visibility', sa.Enum('public', 'members_only', name='community_post_visibility_enum'), nullable=False),
        sa.Column('status', sa.Enum('draft', 'published', name='community_post_status_enum'), nullable=False, server_default='draft'),
        sa.Column('published_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_by_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index('ix_community_posts_status_published_at', 'community_posts', ['status', 'published_at'])

    op.create_table(
        'community_post_email_deliveries',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('post_id', sa.Integer(), sa.ForeignKey('community_posts.id', ondelete='CASCADE'), nullable=False),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column(
            'status',
            sa.Enum('pending', 'sending', 'sent', 'failed', 'skipped', name='community_post_email_status_enum'),
            nullable=False,
            server_default='pending',
        ),
        sa.Column('claimed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('error_code', sa.String(length=64), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint('post_id', 'user_id', name='uq_community_post_delivery_post_user'),
    )
    op.create_index('ix_community_post_deliveries_status', 'community_post_email_deliveries', ['status'])


def downgrade() -> None:
    op.drop_index('ix_community_post_deliveries_status', table_name='community_post_email_deliveries')
    op.drop_table('community_post_email_deliveries')
    op.drop_index('ix_community_posts_status_published_at', table_name='community_posts')
    op.drop_table('community_posts')
    for enum_name in (
        'community_post_email_status_enum',
        'community_post_status_enum',
        'community_post_visibility_enum',
        'community_post_kind_enum',
    ):
        sa.Enum(name=enum_name).drop(op.get_bind(), checkfirst=True)
