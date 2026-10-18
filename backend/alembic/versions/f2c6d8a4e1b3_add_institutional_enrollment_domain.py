"""Store the optional domain of an Institutional Program enrollment.

Revision ID: f2c6d8a4e1b3
Revises: e8c4a6b2d9f0
"""

from alembic import op
import sqlalchemy as sa


revision = "f2c6d8a4e1b3"
down_revision = "e8c4a6b2d9f0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("program_enrollments", sa.Column("domain", sa.String(length=40), nullable=True))
    op.create_check_constraint(
        "ck_program_enrollments_domain", "program_enrollments",
        "domain IS NULL OR domain IN ('Cloud', 'Networking', 'Cybersecurity', "
        "'Data Science', 'AI & ML', 'IoT', 'Quantum Computing', 'Software Engineering', 'BLM')",
    )


def downgrade() -> None:
    count = op.get_bind().execute(sa.text("SELECT count(*) FROM program_enrollments WHERE domain IS NOT NULL")).scalar_one()
    if count:
        raise RuntimeError("Cannot drop assigned Institutional domains. Preserve or explicitly export them before rollback.")
    op.drop_constraint("ck_program_enrollments_domain", "program_enrollments", type_="check")
    op.drop_column("program_enrollments", "domain")
