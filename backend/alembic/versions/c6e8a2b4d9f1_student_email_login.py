"""Email student login and explicit primary program.

Revision ID: c6e8a2b4d9f1
Revises: f9c2b7d4e1a8
"""
from alembic import op
import sqlalchemy as sa

revision = "c6e8a2b4d9f1"
down_revision = "f9c2b7d4e1a8"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    # Check before altering anything. Never rename, merge or delete accounts
    # to force a unique index through a production migration.
    conflicts = bind.execute(sa.text("""
        SELECT count(*) FROM (
            SELECT lower(trim(username)) FROM users
            WHERE username LIKE '%@%'
            GROUP BY lower(trim(username)) HAVING count(*) > 1
        ) conflicts
    """)).scalar_one()
    if conflicts:
        raise RuntimeError(
            "Email-login migration blocked: normalized login duplicates exist. "
            "Review the duplicate preflight in STUDENT_EMAIL_PROVISIONING.md; "
            "resolve identities with account owners before retrying. No accounts were changed."
        )
    op.alter_column("users", "username", existing_type=sa.String(120), type_=sa.String(255), existing_nullable=False)
    op.create_index(
        "uq_users_email_login_normalized", "users", [sa.text("lower(trim(username))")],
        unique=True, postgresql_where=sa.text("username LIKE '%@%'"),
    )
    op.add_column("student_profiles", sa.Column("program_id", sa.Integer(), nullable=True))
    op.create_foreign_key("student_profiles_program_id_fkey", "student_profiles", "programs", ["program_id"], ["id"])


def downgrade():
    # Never truncate newly valid email login IDs to fit an older schema.
    too_long = op.get_bind().execute(sa.text("SELECT count(*) FROM users WHERE length(username) > 120")).scalar_one()
    if too_long:
        raise RuntimeError("Cannot downgrade: login IDs exceed 120 characters. No accounts were changed.")
    op.drop_constraint("student_profiles_program_id_fkey", "student_profiles", type_="foreignkey")
    op.drop_column("student_profiles", "program_id")
    op.drop_index("uq_users_email_login_normalized", table_name="users")
    op.alter_column("users", "username", existing_type=sa.String(255), type_=sa.String(120), existing_nullable=False)
