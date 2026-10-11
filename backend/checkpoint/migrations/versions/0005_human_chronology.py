"""Human chronology independent of background transcription, edited description ownership."""
from alembic import op
import sqlalchemy as sa

revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('work_items', sa.Column('human_updated_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('work_items', sa.Column('description_edited', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.execute('UPDATE work_items SET human_updated_at = COALESCE(updated_at, created_at)')
    op.create_index('ix_work_items_human_updated_at', 'work_items', ['human_updated_at'])


def downgrade():
    op.drop_index('ix_work_items_human_updated_at', table_name='work_items')
    op.drop_column('work_items', 'description_edited')
    op.drop_column('work_items', 'human_updated_at')
