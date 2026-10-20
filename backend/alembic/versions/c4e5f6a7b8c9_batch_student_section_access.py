"""Add independent, disabled-by-default student section access per batch.

Revision ID: c4e5f6a7b8c9
Revises: c2d3e4f5a6b7
"""
from alembic import op
import sqlalchemy as sa

revision = "c4e5f6a7b8c9"
down_revision = "c2d3e4f5a6b7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for name in ("job_applications", "interview_schedule"):
        op.add_column("batches", sa.Column(f"{name}_access_enabled", sa.Boolean(), nullable=False, server_default=sa.false()))
        op.add_column("batches", sa.Column(f"{name}_access_updated_at", sa.DateTime(timezone=True), nullable=True))
        op.add_column("batches", sa.Column(f"{name}_access_updated_by_id", sa.Integer(), nullable=True))
        op.create_foreign_key(f"fk_batches_{name}_access_updated_by_id", "batches", "users", [f"{name}_access_updated_by_id"], ["id"], ondelete="SET NULL")

    bind = op.get_bind()
    bind.execute(sa.text("""
        INSERT INTO permissions (key, module, action, description)
        VALUES ('batches.student_sections_manage', 'Batches', 'student_sections_manage',
                'Enable or disable student Job Applications and Interview Schedule access for a batch.')
        ON CONFLICT (key) DO NOTHING
    """))
    bind.execute(sa.text("""
        INSERT INTO admin_role_permissions (admin_role_id, permission_key)
        SELECT id, 'batches.student_sections_manage' FROM admin_roles WHERE slug = 'admin'
        ON CONFLICT (admin_role_id, permission_key) DO NOTHING
    """))


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text("DELETE FROM admin_role_permissions WHERE permission_key = 'batches.student_sections_manage'"))
    bind.execute(sa.text("DELETE FROM permissions WHERE key = 'batches.student_sections_manage'"))
    for name in ("interview_schedule", "job_applications"):
        op.drop_constraint(f"fk_batches_{name}_access_updated_by_id", "batches", type_="foreignkey")
        op.drop_column("batches", f"{name}_access_updated_by_id")
        op.drop_column("batches", f"{name}_access_updated_at")
        op.drop_column("batches", f"{name}_access_enabled")
