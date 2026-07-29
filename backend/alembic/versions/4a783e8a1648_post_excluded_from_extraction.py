"""post excluded from extraction

Revision ID: 4a783e8a1648
Revises: 959fc1ba1cc5
Create Date: 2026-07-29 08:17:52.589918
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = '4a783e8a1648'
down_revision: str | None = '959fc1ba1cc5'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'post',
        sa.Column(
            'excluded_from_extraction',
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column('post', 'excluded_from_extraction')
