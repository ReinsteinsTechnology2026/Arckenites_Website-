"""enforce unique Community email and normalized mobile

Adds the normalized mobile number to community_profiles and
community_registrations, backfills it from the stored value, and then adds
database-level uniqueness for completed Community accounts:

  - community_profiles.mobile_normalized  UNIQUE
  - lower(community_profiles.email)       UNIQUE

Before any constraint is created, this migration checks for existing
duplicates. If it finds any, it raises and the whole migration rolls back
(Postgres DDL is transactional). Duplicates are listed by user id for manual
resolution. Nothing is deleted or merged automatically.

Revision ID: d5a7c3e1f9b4
Revises: c3e9a1f5b7d2
Create Date: 2026-10-06 00:00:00.000000

"""
import re
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd5a7c3e1f9b4'
down_revision: Union[str, None] = 'c3e9a1f5b7d2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _normalize_mobile(value: str) -> str:
    # Frozen copy of app/core/community_identity.normalize_mobile, so this
    # migration keeps its meaning even if the app helper changes later.
    digits = re.sub(r"\D", "", value or "")
    if digits.startswith("00"):
        digits = digits[2:]
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    return digits


def upgrade() -> None:
    op.add_column('community_profiles', sa.Column('mobile_normalized', sa.String(length=20), nullable=True))
    op.add_column('community_registrations', sa.Column('mobile_normalized', sa.String(length=20), nullable=True))

    conn = op.get_bind()
    for table in ('community_profiles', 'community_registrations'):
        rows = conn.execute(sa.text(f"SELECT id, mobile_number FROM {table}")).all()
        for row_id, mobile in rows:
            conn.execute(
                sa.text(f"UPDATE {table} SET mobile_normalized = :m WHERE id = :id"),
                {"m": _normalize_mobile(mobile), "id": row_id},
            )

    # --- duplicate check: report, never resolve automatically ---
    problems = []

    dup_mobiles = conn.execute(sa.text(
        "SELECT mobile_normalized, array_agg(user_id ORDER BY user_id) "
        "FROM community_profiles GROUP BY mobile_normalized HAVING count(*) > 1"
    )).all()
    for mobile, user_ids in dup_mobiles:
        problems.append(f"duplicate normalized mobile across community_profiles user_ids={list(user_ids)}")

    dup_emails = conn.execute(sa.text(
        "SELECT lower(email), array_agg(user_id ORDER BY user_id) "
        "FROM community_profiles GROUP BY lower(email) HAVING count(*) > 1"
    )).all()
    for _email, user_ids in dup_emails:
        problems.append(f"duplicate email (case-insensitive) across community_profiles user_ids={list(user_ids)}")

    dup_usernames = conn.execute(sa.text(
        "SELECT lower(username), array_agg(id ORDER BY id) "
        "FROM users WHERE role::text = 'community' GROUP BY lower(username) HAVING count(*) > 1"
    )).all()
    for _username, user_ids in dup_usernames:
        problems.append(f"duplicate email (case-insensitive) across community users ids={list(user_ids)}")

    if problems:
        raise RuntimeError(
            "Community identity uniqueness NOT applied: existing duplicates must be resolved manually first. "
            + "; ".join(problems)
        )

    op.alter_column('community_profiles', 'mobile_normalized', existing_type=sa.String(length=20), nullable=False)
    op.create_index('uq_community_profiles_mobile_normalized', 'community_profiles', ['mobile_normalized'], unique=True)
    op.execute("CREATE UNIQUE INDEX uq_community_profiles_email_lower ON community_profiles (lower(email))")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_community_profiles_email_lower")
    op.drop_index('uq_community_profiles_mobile_normalized', table_name='community_profiles')
    op.alter_column('community_profiles', 'mobile_normalized', existing_type=sa.String(length=20), nullable=True)
    op.drop_column('community_registrations', 'mobile_normalized')
    op.drop_column('community_profiles', 'mobile_normalized')
