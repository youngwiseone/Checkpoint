"""Keep item history while retiring old attachments."""
from alembic import op
import sqlalchemy as sa

revision = '0007'
down_revision = '0006'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('work_items', sa.Column('status_changed_at', sa.DateTime(), nullable=True))
    op.add_column('work_items', sa.Column('media_removed_at', sa.DateTime(), nullable=True))
    op.add_column('captures', sa.Column('media_removed_at', sa.DateTime(), nullable=True))
    # Previous versions recorded completion, but no deletion/archive timestamp.
    op.execute("UPDATE work_items SET status_changed_at = CASE "
               "WHEN work_status = 'done' THEN COALESCE(completed_at, human_updated_at, updated_at, created_at) "
               "ELSE COALESCE(human_updated_at, updated_at, created_at) END "
               "WHERE work_status IN ('done', 'wont_do', 'deleted')")


def downgrade():
    op.drop_column('captures', 'media_removed_at')
    op.drop_column('work_items', 'media_removed_at')
    op.drop_column('work_items', 'status_changed_at')
