"""publication reconciliation columns

Revision ID: f3c1d2e4a5b6
Revises: d5b3e7c920af
Create Date: 2026-08-05 03:20:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel

from alembic import op

revision: str = 'f3c1d2e4a5b6'
down_revision: str | None = 'd5b3e7c920af'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Every one of these is nullable with no server default, deliberately. A row written
    # before reconciliation existed was never checked and never notified — NULL says exactly
    # that, where a backfilled timestamp or a `0` would claim a check that never happened.
    op.add_column('publication', sa.Column('checked_at', sa.DateTime(), nullable=True))
    op.add_column(
        'publication',
        sa.Column('remote_status', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    )
    op.add_column(
        'publication', sa.Column('unconfirmed_notified_at', sa.DateTime(), nullable=True)
    )
    op.add_column('publication', sa.Column('failure_notified_at', sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column('publication', 'failure_notified_at')
    op.drop_column('publication', 'unconfirmed_notified_at')
    op.drop_column('publication', 'remote_status')
    op.drop_column('publication', 'checked_at')
