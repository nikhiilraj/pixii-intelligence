"""publication table and draft.revision

Revision ID: d5b3e7c920af
Revises: a4f2c81de930
Create Date: 2026-08-05 01:10:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel

from alembic import op

revision: str = 'd5b3e7c920af'
down_revision: str | None = 'a4f2c81de930'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Existing drafts start at revision 1, the same as a new one. Nothing has been published
    # from Pixii yet, so there is no command in flight whose revision this could contradict.
    op.add_column(
        'draft',
        sa.Column('revision', sa.Integer(), nullable=False, server_default='1'),
    )

    op.create_table(
        'publication',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('draft_id', sa.Integer(), nullable=False),
        sa.Column('draft_revision', sa.Integer(), nullable=False),
        sa.Column('action', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        # Nullable for publish_now and cancel_schedule, which name no future time. An absent
        # time, not a zeroed one — midnight is a time somebody could have meant.
        sa.Column('requested_local_time', sa.DateTime(), nullable=True),
        sa.Column('timezone', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('scheduled_utc', sa.DateTime(), nullable=True),
        sa.Column('idempotency_key', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('state', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('last_error', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('accepted_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['draft_id'], ['draft.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    # The duplicate guard, and the reason it is a constraint rather than a check in code: a
    # read-then-insert races with itself under a double-click, which is the exact input this
    # is defending against.
    op.create_index(
        op.f('ix_publication_idempotency_key'),
        'publication',
        ['idempotency_key'],
        unique=True,
    )
    op.create_index(op.f('ix_publication_draft_id'), 'publication', ['draft_id'], unique=False)
    op.create_index(op.f('ix_publication_action'), 'publication', ['action'], unique=False)
    op.create_index(op.f('ix_publication_state'), 'publication', ['state'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_publication_state'), table_name='publication')
    op.drop_index(op.f('ix_publication_action'), table_name='publication')
    op.drop_index(op.f('ix_publication_draft_id'), table_name='publication')
    op.drop_index(op.f('ix_publication_idempotency_key'), table_name='publication')
    op.drop_table('publication')
    op.drop_column('draft', 'revision')
