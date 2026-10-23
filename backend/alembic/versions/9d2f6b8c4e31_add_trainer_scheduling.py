"""add trainer availability calendar and batch assignment history

Revision ID: 9d2f6b8c4e31
Revises: 9c1e5a7b3d20

Adds only new tables and three permission rows; no existing table or column
is altered, so the previous application version keeps working against the
upgraded schema. Downgrade drops the new tables (availability data is lost).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "9d2f6b8c4e31"
down_revision: Union[str, None] = "9c1e5a7b3d20"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

PERMISSIONS = [
    ("scheduling.view", "view", "View trainer availability, workload and assignment history."),
    ("scheduling.manage", "manage", "Assign or change a batch's trainer, manage trainer expertise and request availability changes."),
    ("scheduling.override", "override", "Override an availability, leave or workload conflict when assigning a trainer (a reason is recorded)."),
]


def upgrade() -> None:
    op.create_table(
        "trainer_availability_rules",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("trainer_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("weekday", sa.SmallInteger(), nullable=False),
        sa.Column("start_time", sa.Time(), nullable=False),
        sa.Column("end_time", sa.Time(), nullable=False),
        sa.Column("kind", sa.String(12), nullable=False, server_default="available"),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_until", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.CheckConstraint("weekday BETWEEN 0 AND 6", name="ck_tar_weekday"),
        sa.CheckConstraint("end_time > start_time", name="ck_tar_time_order"),
        sa.CheckConstraint("kind IN ('available', 'preferred')", name="ck_tar_kind"),
        sa.CheckConstraint("effective_until IS NULL OR effective_until >= effective_from", name="ck_tar_date_order"),
    )
    op.create_index("ix_tar_trainer_weekday", "trainer_availability_rules", ["trainer_id", "weekday"])

    op.create_table(
        "trainer_availability_entries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("trainer_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(12), nullable=False),
        sa.Column("category", sa.String(20), nullable=True),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("note", sa.String(300), nullable=True),
        sa.Column("created_by_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.CheckConstraint("ends_at > starts_at", name="ck_tae_time_order"),
        sa.CheckConstraint("kind IN ('available', 'preferred', 'unavailable', 'leave')", name="ck_tae_kind"),
    )
    op.create_index("ix_tae_trainer_window", "trainer_availability_entries", ["trainer_id", "starts_at", "ends_at"])

    op.create_table(
        "trainer_expertise",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("trainer_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("program_id", sa.Integer(), sa.ForeignKey("programs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("trainer_id", "program_id", name="uq_trainer_expertise"),
    )
    op.create_index("ix_trainer_expertise_trainer_id", "trainer_expertise", ["trainer_id"])
    op.create_index("ix_trainer_expertise_program_id", "trainer_expertise", ["program_id"])

    op.create_table(
        "batch_trainer_assignments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("batch_id", sa.Integer(), sa.ForeignKey("batches.id", ondelete="CASCADE"), nullable=False),
        sa.Column("action", sa.String(12), nullable=False),
        sa.Column("from_trainer_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("to_trainer_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("actor_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("was_override", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("overridden_conflicts", sa.JSON(), nullable=True),
        sa.Column("idempotency_key", sa.String(64), nullable=True, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.CheckConstraint("action IN ('assigned', 'changed', 'unassigned')", name="ck_bta_action"),
    )
    op.create_index("ix_bta_batch_created", "batch_trainer_assignments", ["batch_id", "created_at"])
    op.create_index("ix_batch_trainer_assignments_to_trainer_id", "batch_trainer_assignments", ["to_trainer_id"])

    op.create_table(
        "scheduling_notices",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("recipient_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=True),
        sa.Column("trainer_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=True),
        sa.Column("batch_id", sa.Integer(), sa.ForeignKey("batches.id", ondelete="CASCADE"), nullable=True),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(10), nullable=False, server_default="open"),
        sa.Column("created_by_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('open', 'resolved')", name="ck_sn_status"),
    )
    op.create_index("ix_sn_recipient_status", "scheduling_notices", ["recipient_id", "status"])
    op.create_index("ix_scheduling_notices_trainer_id", "scheduling_notices", ["trainer_id"])

    bind = op.get_bind()
    for key, action, description in PERMISSIONS:
        bind.execute(
            sa.text("INSERT INTO permissions (key, module, action, description) VALUES (:k, 'Scheduling', :a, :d) ON CONFLICT (key) DO NOTHING"),
            {"k": key, "a": action, "d": description},
        )
    # Default grants mirror crud/permissions.py DEFAULT_GRANTS; override stays Super Admin (computed) only.
    for role_slug, keys in (("admin", ("scheduling.view", "scheduling.manage")), ("support_admin", ("scheduling.view",))):
        for key in keys:
            bind.execute(
                sa.text("INSERT INTO admin_role_permissions (admin_role_id, permission_key) "
                        "SELECT id, :k FROM admin_roles WHERE slug = :s ON CONFLICT (admin_role_id, permission_key) DO NOTHING"),
                {"k": key, "s": role_slug},
            )


def downgrade() -> None:
    bind = op.get_bind()
    keys = [p[0] for p in PERMISSIONS]
    bind.execute(sa.text("DELETE FROM admin_role_permissions WHERE permission_key = ANY(:keys)"), {"keys": keys})
    bind.execute(sa.text("DELETE FROM permissions WHERE key = ANY(:keys)"), {"keys": keys})
    op.drop_table("scheduling_notices")
    op.drop_table("batch_trainer_assignments")
    op.drop_table("trainer_expertise")
    op.drop_table("trainer_availability_entries")
    op.drop_table("trainer_availability_rules")
