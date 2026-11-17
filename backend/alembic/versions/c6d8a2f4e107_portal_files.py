"""Private portal storage metadata on production revision 9d2f6b8c4e31."""
from alembic import op
import sqlalchemy as sa
revision = 'c6d8a2f4e107'
down_revision = '9d2f6b8c4e31'
branch_labels = None
depends_on = None

def upgrade():
    op.create_table('portal_files',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('scope', sa.String(16), nullable=False),
        sa.Column('target_id', sa.Integer(), nullable=False),
        sa.Column('uploader_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL')),
        sa.Column('drive_id', sa.String(300), nullable=False),
        sa.Column('folder', sa.String(300), nullable=False),
        sa.Column('remote_name', sa.String(100), nullable=False),
        sa.Column('remote_item_id', sa.String(300)),
        sa.Column('file_name', sa.String(255), nullable=False),
        sa.Column('file_type', sa.String(150), nullable=False),
        sa.Column('size_bytes', sa.Integer(), nullable=False),
        sa.Column('state', sa.String(16), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    op.create_index('ix_portal_files_scope', 'portal_files', ['scope', 'target_id', 'state'])

def downgrade():
    # Remote files are not deleted by schema rollback. Export metadata first.
    op.drop_index('ix_portal_files_scope', table_name='portal_files')
    op.drop_table('portal_files')
