"""add VM Lab Access tables, drop old plaintext lab_access, seed lab_vm_admin role

Revision ID: b3d6f1a9c4e2
Revises: 8a2f4c6e9b1d
Create Date: 2026-09-17 00:00:00.000000

Replaces the old batch_resources.LabAccess feature (per-batch RDP
host/username/password shown as plaintext to students) with the new VM
Lab Access system: an admin-managed VM inventory, time-boxed grants
enforced server-side (and, separately, by the Windows Agent on the VM
itself), and a dedicated audit trail. See the VM Lab Access plan for the
full design.

The drop of the old `lab_access` table is self-protecting: it checks the
live row count first and aborts loudly instead of assuming the table is
safe to drop. Locally and in this project's own testing it has always
been empty; production has not been independently confirmed, which is
exactly why this check exists rather than skipping it.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'b3d6f1a9c4e2'
down_revision: Union[str, None] = '8a2f4c6e9b1d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    # --- Drop the old plaintext-RDP lab_access table, but only if empty ---
    row_count = conn.execute(sa.text("SELECT COUNT(*) FROM lab_access")).scalar()
    if row_count:
        raise RuntimeError(
            f"Refusing to drop 'lab_access': it has {row_count} row(s). "
            "This migration only removes the old plaintext-RDP lab_access "
            "feature when it is empty. Export/migrate this data first, "
            "then re-run."
        )
    op.drop_index(op.f('ix_lab_access_batch_id'), table_name='lab_access')
    op.drop_table('lab_access')

    # --- New VM inventory ---
    op.create_table(
        'lab_vms',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('name', sa.String(length=150), nullable=False),
        sa.Column('hostname', sa.String(length=255), nullable=False),
        sa.Column('rdp_port', sa.Integer(), nullable=False, server_default='3389'),
        sa.Column('operating_system', sa.String(length=100), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('student_rdp_username', sa.String(length=150), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('agent_token_hash', sa.String(length=64), nullable=False),
        sa.Column('agent_last_seen_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index('ix_lab_vms_agent_token_hash', 'lab_vms', ['agent_token_hash'], unique=True)

    # --- Access grants ---
    lab_vm_access_status_enum = sa.Enum('active', 'expired', 'revoked', name='lab_vm_access_status_enum')

    op.create_table(
        'lab_vm_access',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('student_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('vm_id', sa.Integer(), sa.ForeignKey('lab_vms.id'), nullable=False),
        sa.Column('status', lab_vm_access_status_enum, nullable=False, server_default='active'),
        sa.Column('granted_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('granted_by', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('revoked_by', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index('ix_lab_vm_access_student_id', 'lab_vm_access', ['student_id'])
    op.create_index('ix_lab_vm_access_vm_id', 'lab_vm_access', ['vm_id'])
    op.create_index('ix_lab_vm_access_status', 'lab_vm_access', ['status'])
    op.create_index('ix_lab_vm_access_expires_at', 'lab_vm_access', ['expires_at'])

    # The real race-condition backstop: at most one 'active' row per
    # student, enforced by Postgres itself (not just application logic),
    # so a double-clicked Grant button can never produce two active grants.
    op.create_index(
        'uq_one_active_lab_vm_access_per_student',
        'lab_vm_access',
        ['student_id'],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )

    # --- Audit log ---
    op.create_table(
        'lab_vm_access_audit_log',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('student_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('vm_id', sa.Integer(), sa.ForeignKey('lab_vms.id'), nullable=True),
        sa.Column('action', sa.String(length=30), nullable=False),
        sa.Column('performed_by', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('reason', sa.String(length=500), nullable=True),
        sa.Column('event_metadata', postgresql.JSONB(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index('ix_lab_vm_access_audit_log_student_id', 'lab_vm_access_audit_log', ['student_id'])
    op.create_index('ix_lab_vm_access_audit_log_vm_id', 'lab_vm_access_audit_log', ['vm_id'])
    op.create_index('ix_lab_vm_access_audit_log_created_at', 'lab_vm_access_audit_log', ['created_at'])

    # --- Seed the Lab VM Admin role (row only — grants come from seed.py's
    # DEFAULT_GRANTS, same convention as 8a2f4c6e9b1d) ---
    op.execute(
        """
        INSERT INTO admin_roles (name, slug, description, is_system)
        VALUES (
            'Lab VM Admin',
            'lab_vm_admin',
            'Manages the VM lab inventory and grants/revokes student RDP access. Can only be assigned by a Super Admin.',
            true
        )
        """
    )


def downgrade() -> None:
    op.execute("DELETE FROM admin_role_permissions WHERE admin_role_id = (SELECT id FROM admin_roles WHERE slug = 'lab_vm_admin')")
    op.execute("DELETE FROM admin_roles WHERE slug = 'lab_vm_admin'")

    op.drop_index('ix_lab_vm_access_audit_log_created_at', table_name='lab_vm_access_audit_log')
    op.drop_index('ix_lab_vm_access_audit_log_vm_id', table_name='lab_vm_access_audit_log')
    op.drop_index('ix_lab_vm_access_audit_log_student_id', table_name='lab_vm_access_audit_log')
    op.drop_table('lab_vm_access_audit_log')

    op.drop_index('uq_one_active_lab_vm_access_per_student', table_name='lab_vm_access')
    op.drop_index('ix_lab_vm_access_expires_at', table_name='lab_vm_access')
    op.drop_index('ix_lab_vm_access_status', table_name='lab_vm_access')
    op.drop_index('ix_lab_vm_access_vm_id', table_name='lab_vm_access')
    op.drop_index('ix_lab_vm_access_student_id', table_name='lab_vm_access')
    op.drop_table('lab_vm_access')
    sa.Enum(name='lab_vm_access_status_enum').drop(op.get_bind(), checkfirst=True)

    op.drop_index('ix_lab_vms_agent_token_hash', table_name='lab_vms')
    op.drop_table('lab_vms')

    # Recreate the old lab_access table so a downgrade leaves the schema
    # consistent with c9f2a4e7b3d8 (data is not restored — it was verified
    # empty before the drop in upgrade()).
    op.create_table(
        'lab_access',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('batch_id', sa.Integer(), sa.ForeignKey('batches.id'), nullable=False),
        sa.Column('title', sa.String(length=200), nullable=False),
        sa.Column('access_url', sa.String(length=500), nullable=False),
        sa.Column('username', sa.String(length=200), nullable=True),
        sa.Column('password', sa.String(length=200), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    )
    op.create_index(op.f('ix_lab_access_batch_id'), 'lab_access', ['batch_id'], unique=False)
