"""draft asset_values

Revision ID: f41b8a1ac8ca
Revises: 443990bee2a6
Create Date: 2026-07-30 11:49:02.289080
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = 'f41b8a1ac8ca'
down_revision: str | None = '443990bee2a6'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # The assets picked for a draft's `image_url` slots. Additive; nothing is rewritten.
    #
    # `server_default` on a non-nullable add is what backfills the rows that predate the
    # column — every draft generated before this slice gets `{}` rather than NULL, so no
    # reader has to guard for a missing dict. The default stays on the column afterwards
    # so a hand-written INSERT cannot reintroduce a NULL.
    op.add_column(
        'draft',
        sa.Column(
            'asset_values',
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column('draft', 'asset_values')
