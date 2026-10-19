"""Add admin-controlled job announcements and one reaction per student.

Revision ID: b1c2d3e4f5a6
Revises: a9d4e7f1c2b6
"""
from alembic import op
import sqlalchemy as sa

revision = "b1c2d3e4f5a6"
down_revision = "a9d4e7f1c2b6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # These values are first used by API requests after this migration commits.
    for value in ("job_application_created", "job_application_updated", "job_application_status_changed"):
        op.execute(f"ALTER TYPE auth_event_type ADD VALUE IF NOT EXISTS '{value}'")

    status = sa.Enum("draft", "published", "archived", name="job_application_status_enum")
    reaction = sa.Enum("like", "love", "celebrate", "interested", name="job_reaction_kind_enum")
    op.create_table(
        "job_applications",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("company", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("eligibility", sa.Text()),
        sa.Column("application_url", sa.String(2048), nullable=False),
        sa.Column("deadline", sa.Date()),
        sa.Column("status", status, nullable=False, server_default="draft"),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("created_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_job_applications_visibility", "job_applications", ["status", "is_enabled", "published_at"])
    op.create_table(
        "job_application_reactions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("job_id", sa.Integer(), sa.ForeignKey("job_applications.id", ondelete="CASCADE"), nullable=False),
        sa.Column("student_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", reaction, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("job_id", "student_id", name="uq_job_reaction_student"),
    )
    op.create_index("ix_job_reactions_job_kind", "job_application_reactions", ["job_id", "kind"])


def downgrade() -> None:
    bind = op.get_bind()
    if bind.execute(sa.text("SELECT count(*) FROM job_applications")).scalar_one():
        raise RuntimeError("Cannot remove job applications while announcements exist")
    op.drop_index("ix_job_reactions_job_kind", table_name="job_application_reactions")
    op.drop_table("job_application_reactions")
    op.drop_index("ix_job_applications_visibility", table_name="job_applications")
    op.drop_table("job_applications")
    sa.Enum(name="job_reaction_kind_enum").drop(bind, checkfirst=True)
    sa.Enum(name="job_application_status_enum").drop(bind, checkfirst=True)
    # PostgreSQL cannot remove values from auth_event_type.
