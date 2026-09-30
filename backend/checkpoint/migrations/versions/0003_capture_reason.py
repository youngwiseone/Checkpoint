"""why an auto screenshot was taken

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-01 10:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0003'
down_revision: Union[str, None] = '0002'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('captures', sa.Column('reason', sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('captures') as b:
        b.drop_column('reason')
