"""daily_run table

Revision ID: a4f2c81de930
Revises: 6910a95bf6be
Create Date: 2026-08-05 00:40:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel

from alembic import op

revision: str = 'a4f2c81de930'
down_revision: str | None = '6910a95bf6be'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'daily_run',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('run_date', sa.Date(), nullable=False),
        sa.Column('slot', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('status', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('started_at', sa.DateTime(), nullable=False),
        sa.Column('finished_at', sa.DateTime(), nullable=True),
        # Nullable counts, deliberately: NULL is "the run has not finished, nobody counted",
        # which `0` would misreport as "it finished and produced nothing".
        sa.Column('drafts_created', sa.Integer(), nullable=True),
        sa.Column('topics_failed', sa.Integer(), nullable=True),
        sa.Column('visuals_failed', sa.Integer(), nullable=True),
        sa.Column('error', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('notified_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        # The lock. `INSERT … ON CONFLICT DO NOTHING` against this constraint is what stops
        # two API processes from running the same day twice; without it the app has no
        # mutual exclusion at all.
        sa.UniqueConstraint('run_date', 'slot', name='uq_daily_run_date_slot'),
    )
    op.create_index(op.f('ix_daily_run_run_date'), 'daily_run', ['run_date'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_daily_run_run_date'), table_name='daily_run')
    op.drop_table('daily_run')
