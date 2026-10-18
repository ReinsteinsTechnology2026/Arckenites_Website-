"""Add opt-in TOTP MFA without changing existing accounts or sessions.

Revision ID: a9d4e7f1c2b6
Revises: f2c6d8a4e1b3
"""
from alembic import op
import sqlalchemy as sa

revision = "a9d4e7f1c2b6"
down_revision = "f2c6d8a4e1b3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("system_settings", sa.Column("mfa_required", sa.Boolean(), nullable=False,
                                                server_default=sa.false()))
    op.add_column("auth_sessions", sa.Column("mfa_verified", sa.Boolean(), nullable=False,
                                             server_default=sa.false()))
    op.create_table(
        "mfa_credentials",
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("secret_ciphertext", sa.String(500)),
        sa.Column("pending_ciphertext", sa.String(500)),
        sa.Column("pending_expires_at", sa.DateTime(timezone=True)),
        sa.Column("pending_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("enabled_at", sa.DateTime(timezone=True)),
        sa.Column("last_used_step", sa.Integer()),
    )
    op.create_table(
        "mfa_recovery_codes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("code_hash", sa.String(64), nullable=False, unique=True),
    )
    op.create_index("ix_mfa_recovery_codes_user_id", "mfa_recovery_codes", ["user_id"])
    op.create_table(
        "mfa_challenges",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True)),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )
    op.create_index("ix_mfa_challenges_user_id", "mfa_challenges", ["user_id"])


def downgrade() -> None:
    bind = op.get_bind()
    for table in ("mfa_credentials", "mfa_recovery_codes", "mfa_challenges"):
        if bind.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one():
            raise RuntimeError("Cannot remove MFA schema while account or challenge data exists")
    if bind.execute(sa.text("SELECT count(*) FROM system_settings WHERE mfa_required = true")).scalar_one():
        raise RuntimeError("Cannot remove MFA schema while mandatory MFA policy is enabled")
    if bind.execute(sa.text("SELECT count(*) FROM auth_sessions WHERE mfa_verified = true")).scalar_one():
        raise RuntimeError("Cannot remove MFA schema while MFA sessions exist")
    op.drop_index("ix_mfa_challenges_user_id", table_name="mfa_challenges")
    op.drop_table("mfa_challenges")
    op.drop_index("ix_mfa_recovery_codes_user_id", table_name="mfa_recovery_codes")
    op.drop_table("mfa_recovery_codes")
    op.drop_table("mfa_credentials")
    op.drop_column("auth_sessions", "mfa_verified")
    op.drop_column("system_settings", "mfa_required")
