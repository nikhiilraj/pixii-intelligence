"""draft went_live_at

Revision ID: b7c31d90e4a2
Revises: f41b8a1ac8ca
Create Date: 2026-07-30 12:16:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = 'b7c31d90e4a2'
down_revision: str | None = 'f41b8a1ac8ca'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # When a pushed draft was observed live on the platform. Additive.
    #
    # **Named `went_live_at`, not `published_at`, deliberately.** `Post` already has a
    # `published_at`, and a `select(Draft, Post)` merge with two identically-named columns
    # resolves to whichever the query happens to pick — wrong silently, with no error. The
    # different name makes that class of bug impossible to write.
    #
    # Nullable with no server_default, because NULL is the meaningful state: pushed but not
    # yet live. Backfilling it to any timestamp would claim a publication that never happened.
    op.add_column('draft', sa.Column('went_live_at', sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column('draft', 'went_live_at')
