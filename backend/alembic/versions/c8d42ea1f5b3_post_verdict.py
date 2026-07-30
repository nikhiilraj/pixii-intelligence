"""post verdict

Revision ID: c8d42ea1f5b3
Revises: b7c31d90e4a2
Create Date: 2026-07-30 12:16:30.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = 'c8d42ea1f5b3'
down_revision: str | None = 'b7c31d90e4a2'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # A human's ruling on a published post: worked / didnt / mixed, plus why.
    #
    # Engagement spans 12.7x across this corpus at ~3 samples per template, so no aggregate
    # can rank anything yet. A verdict is the one form of learning that is honest at n=1 —
    # it claims judgement, not statistics.
    #
    # `verdict` is nullable because NULL is the queue: a published post awaiting a ruling.
    # A CHECK constraint is deliberately omitted — the route validates against the StrEnum,
    # and a database constraint would make adding a fourth verdict a migration rather than
    # an edit. ponytail: revisit if anything ever writes this table outside the route.
    op.add_column('post', sa.Column('verdict', sa.String(), nullable=True))
    op.add_column(
        'post',
        sa.Column('verdict_note', sa.String(), nullable=False, server_default=sa.text("''")),
    )
    op.add_column('post', sa.Column('verdict_at', sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column('post', 'verdict_at')
    op.drop_column('post', 'verdict_note')
    op.drop_column('post', 'verdict')
