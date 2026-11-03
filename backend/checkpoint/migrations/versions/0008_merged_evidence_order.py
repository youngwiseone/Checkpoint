"""Preserve attachment order when tasks from different recordings are merged."""
from alembic import op
import sqlalchemy as sa

revision = '0008'
down_revision = '0007'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('evidence_links', sa.Column('work_item_order', sa.Integer(), nullable=True))


def downgrade():
    op.drop_column('evidence_links', 'work_item_order')
