"""project bindings, session branches and agent runs

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-01 12:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0004'
down_revision: Union[str, None] = '0003'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('projects', sa.Column('repo_path', sa.String(length=1000), nullable=True))
    op.add_column('projects', sa.Column('base_branch', sa.String(length=200), nullable=True))
    op.add_column('projects', sa.Column('default_agent', sa.String(length=20), nullable=False, server_default='none'))
    op.add_column('projects', sa.Column('agent_access', sa.String(length=20), nullable=False, server_default='standard'))
    op.add_column('projects', sa.Column('setup_command', sa.Text(), nullable=False, server_default=''))
    op.add_column('projects', sa.Column('check_command', sa.Text(), nullable=False, server_default=''))
    op.add_column('projects', sa.Column('preview_command', sa.Text(), nullable=False, server_default=''))
    op.add_column('projects', sa.Column('preview_url', sa.String(length=500), nullable=False, server_default=''))

    op.add_column('sessions', sa.Column('name_locked', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column('sessions', sa.Column('name_edited', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column('sessions', sa.Column('branch', sa.String(length=200), nullable=True))
    op.add_column('sessions', sa.Column('worktree_path', sa.String(length=1000), nullable=True))
    op.add_column('sessions', sa.Column('base_commit', sa.String(length=64), nullable=True))
    op.add_column('sessions', sa.Column('agent_session_id', sa.String(length=100), nullable=True))
    op.add_column('sessions', sa.Column('pr_url', sa.String(length=500), nullable=True))

    op.add_column('work_items', sa.Column('task_number', sa.Integer(), nullable=True))
    op.add_column('work_items', sa.Column('agent_state', sa.String(length=20), nullable=True))
    op.add_column('work_items', sa.Column('agent_note', sa.Text(), nullable=True))
    op.add_column('work_items', sa.Column('agent_run_id', sa.String(length=36), nullable=True))
    op.add_column('work_items', sa.Column('agent_commit', sa.String(length=64), nullable=True))
    op.add_column('work_items', sa.Column('sent_at', sa.DateTime(), nullable=True))
    op.add_column('work_items', sa.Column('duplicate_of_id', sa.String(length=36), nullable=True))
    op.create_index('ix_work_items_agent_state', 'work_items', ['agent_state'])

    op.create_table(
        'agent_runs',
        sa.Column('id', sa.String(length=36), primary_key=True),
        sa.Column('session_id', sa.String(length=36), sa.ForeignKey('sessions.id', ondelete='CASCADE'), nullable=False),
        sa.Column('project_id', sa.String(length=36), sa.ForeignKey('projects.id', ondelete='CASCADE'), nullable=False),
        sa.Column('agent', sa.String(length=20), nullable=False),
        sa.Column('state', sa.String(length=20), nullable=False),
        sa.Column('stage', sa.String(length=300), nullable=False, server_default=''),
        sa.Column('item_ids', sa.JSON(), nullable=False),
        sa.Column('handoff_id', sa.String(length=36), nullable=True),
        sa.Column('prompt', sa.Text(), nullable=False, server_default=''),
        sa.Column('pid', sa.Integer(), nullable=True),
        sa.Column('agent_session_id', sa.String(length=100), nullable=True),
        sa.Column('log_path', sa.String(length=1000), nullable=True),
        sa.Column('head_before', sa.String(length=64), nullable=True),
        sa.Column('head_after', sa.String(length=64), nullable=True),
        sa.Column('summary', sa.Text(), nullable=True),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('accepted_at', sa.DateTime(), nullable=True),
        sa.Column('finished_at', sa.DateTime(), nullable=True),
    )
    op.create_index('ix_agent_runs_session_id', 'agent_runs', ['session_id'])
    op.create_index('ix_agent_runs_state', 'agent_runs', ['state'])


def downgrade() -> None:
    op.drop_index('ix_agent_runs_state', 'agent_runs')
    op.drop_index('ix_agent_runs_session_id', 'agent_runs')
    op.drop_table('agent_runs')
    op.drop_index('ix_work_items_agent_state', 'work_items')
    with op.batch_alter_table('work_items') as b:
        for c in ('duplicate_of_id', 'sent_at', 'agent_commit', 'agent_run_id', 'agent_note', 'agent_state', 'task_number'):
            b.drop_column(c)
    with op.batch_alter_table('sessions') as b:
        for c in ('pr_url', 'agent_session_id', 'base_commit', 'worktree_path', 'branch', 'name_edited', 'name_locked'):
            b.drop_column(c)
    with op.batch_alter_table('projects') as b:
        for c in ('preview_url', 'preview_command', 'check_command', 'setup_command', 'agent_access', 'default_agent',
                  'base_branch', 'repo_path'):
            b.drop_column(c)
