"""Separate Community and managed portal email-login scopes.

No account data is rewritten. Apply only after review, with old application
workers stopped: old workers resolve usernames without a login context.
"""
from alembic import op
import sqlalchemy as sa

revision = "d8e9f0a1b2c3"
down_revision = "c4e5f6a7b8c9"
branch_labels = None
depends_on = None

SCOPE = "(CASE WHEN role = 'community' THEN 'community' ELSE 'portal' END)"


def upgrade():
    bind = op.get_bind()
    # Inspect actual constraints too: installations with schema drift must not
    # silently retain an additional global uniqueness restriction.
    inspector = sa.inspect(bind)
    constraints = inspector.get_unique_constraints("users")
    indexes = inspector.get_indexes("users")
    if any(c.get("column_names") == ["username"] for c in constraints):
        raise RuntimeError("Unexpected username unique constraint; review schema drift before migration.")
    if any(i.get("unique") and i.get("column_names") == ["username"]
           and i["name"] != "ix_users_username" for i in indexes):
        raise RuntimeError("Unexpected username unique index; review schema drift before migration.")
    op.drop_index("ix_users_username", table_name="users")
    op.drop_index("uq_users_email_login_normalized", table_name="users")
    op.create_index("ix_users_username", "users", ["username"], unique=False)
    op.create_index("uq_users_legacy_username", "users", ["username"], unique=True,
                    postgresql_where=sa.text("username NOT LIKE '%@%'"))
    op.create_index("uq_users_email_login_normalized", "users",
                    [sa.text(SCOPE), sa.text("lower(trim(username))")], unique=True,
                    postgresql_where=sa.text("username LIKE '%@%'"))


def downgrade():
    # Reinstating global uniqueness can be impossible after legitimate reuse.
    # Refuse before DDL; never delete, rename, or merge users to make it fit.
    conflicts = op.get_bind().execute(sa.text("""
        SELECT count(*) FROM (
            SELECT lower(trim(username)) FROM users WHERE username LIKE '%@%'
            GROUP BY lower(trim(username)) HAVING count(*) > 1
        ) conflicts
    """)).scalar_one()
    if conflicts:
        raise RuntimeError("Cannot downgrade: cross-scope email logins exist. No accounts were changed.")
    op.drop_index("uq_users_email_login_normalized", table_name="users")
    op.drop_index("uq_users_legacy_username", table_name="users")
    op.drop_index("ix_users_username", table_name="users")
    op.create_index("ix_users_username", "users", ["username"], unique=True)
    op.create_index("uq_users_email_login_normalized", "users",
                    [sa.text("lower(trim(username))")], unique=True,
                    postgresql_where=sa.text("username LIKE '%@%'"))
