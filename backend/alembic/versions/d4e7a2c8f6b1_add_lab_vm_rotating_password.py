"""add rotating RDP password columns to lab_vms and lab_vm_access

Revision ID: d4e7a2c8f6b1
Revises: b3d6f1a9c4e2
Create Date: 2026-09-17 00:00:00.000000

Adds encrypted-at-rest storage for the VM Lab Access system's short-lived,
auto-rotating student RDP password — never the VM's real Administrator
password. Both columns are nullable Text (Fernet ciphertext, never
plaintext); see app/core/lab_vm_secrets.py.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd4e7a2c8f6b1'
down_revision: Union[str, None] = 'b3d6f1a9c4e2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('lab_vms', sa.Column('current_agent_password_encrypted', sa.Text(), nullable=True))
    op.add_column('lab_vm_access', sa.Column('rdp_password_encrypted', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('lab_vm_access', 'rdp_password_encrypted')
    op.drop_column('lab_vms', 'current_agent_password_encrypted')
