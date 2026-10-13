"""Retained voice notes and explicit automation feedback."""
from alembic import op
import sqlalchemy as sa

revision = '0006'
down_revision = '0005'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('notes', sa.Column('audio_rel_path', sa.String(500), nullable=True))
    op.add_column('notes', sa.Column('speech_words', sa.JSON(), nullable=True))
    op.add_column('work_items', sa.Column('feedback_reason', sa.String(30), nullable=True))


def downgrade():
    op.drop_column('work_items', 'feedback_reason')
    op.drop_column('notes', 'audio_rel_path')
    op.drop_column('notes', 'speech_words')
