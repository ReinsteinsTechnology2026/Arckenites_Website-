"""add Google sign-in tables for existing portal accounts

Two new tables, both additive:

  user_google_identities  the Google `sub` linked to one portal account,
                          so later sign-ins match on the stable Google id
  google_login_tickets    one-time, short-lived tickets handed from the
                          OAuth callback to the login page; only a SHA-256
                          hash of each ticket is stored

No existing table is altered. Community tables are not touched. Downgrading
drops only these two tables.

Revision ID: f9c2b7d4e1a8
Revises: e7b1d4c9a2f6
Create Date: 2026-10-06 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f9c2b7d4e1a8'
down_revision: Union[str, None] = 'e7b1d4c9a2f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'user_google_identities',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False, unique=True),
        sa.Column('google_sub', sa.String(length=255), nullable=False, unique=True),
        sa.Column('linked_email', sa.String(length=255), nullable=False),
        sa.Column('linked_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        'google_login_tickets',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('token_hash', sa.String(length=64), nullable=False, unique=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('used_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table('google_login_tickets')
    op.drop_table('user_google_identities')
