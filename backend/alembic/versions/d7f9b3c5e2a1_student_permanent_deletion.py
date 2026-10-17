"""Retain shared student history with detached identity; durable file cleanup.

Revision ID: d7f9b3c5e2a1
Revises: c6e8a2b4d9f1
"""
from alembic import op
import sqlalchemy as sa

revision = "d7f9b3c5e2a1"
down_revision = "c6e8a2b4d9f1"
branch_labels = None
depends_on = None

COLUMNS = (
    ("conversations", "user_a_id"), ("conversations", "user_b_id"),
    ("direct_messages", "sender_id"), ("batch_messages", "sender_id"),
    ("support_tickets", "requester_id"), ("support_messages", "sender_id"),
    ("lab_access_audit_log", "student_id"), ("lab_vm_access_audit_log", "student_id"),
)


def upgrade():
    for table, column in COLUMNS:
        op.alter_column(table, column, existing_type=sa.Integer(), nullable=True)
    op.create_table("file_cleanup_jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("storage_kind", sa.String(30), nullable=False),
        sa.Column("storage_reference", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade():
    connection = op.get_bind()
    # Never discard history or cleanup jobs to make a downgrade succeed.
    if connection.scalar(sa.text("SELECT count(*) FROM file_cleanup_jobs")):
        raise RuntimeError("Pending file cleanup jobs must finish before downgrade")
    for table, column in COLUMNS:
        if connection.scalar(sa.text(f'SELECT count(*) FROM "{table}" WHERE "{column}" IS NULL')):
            raise RuntimeError("Detached student history exists; downgrade requires an explicit restoration plan")
    op.drop_table("file_cleanup_jobs")
    for table, column in reversed(COLUMNS):
        op.alter_column(table, column, existing_type=sa.Integer(), nullable=False)
