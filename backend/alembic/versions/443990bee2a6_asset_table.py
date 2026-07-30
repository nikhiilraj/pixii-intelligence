"""asset table

Revision ID: 443990bee2a6
Revises: 4a783e8a1648
Create Date: 2026-07-30 10:47:14.504372
"""
from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = '443990bee2a6'
down_revision: str | None = '4a783e8a1648'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('asset',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('filename', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('label', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column(
        'kind',
        sa.Enum('LOGO', 'PRODUCT', 'SCREENSHOT', 'BRAND', 'PHOTO', name='assetkind'),
        nullable=False,
    ),
    sa.Column('tags', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('width', sa.Integer(), nullable=False),
    sa.Column('height', sa.Integer(), nullable=False),
    sa.Column('sha256', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('source_post_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['source_post_id'], ['post.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_asset_kind'), 'asset', ['kind'], unique=False)
    op.create_index(op.f('ix_asset_sha256'), 'asset', ['sha256'], unique=True)
    op.create_index(op.f('ix_asset_source_post_id'), 'asset', ['source_post_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_asset_source_post_id'), table_name='asset')
    op.drop_index(op.f('ix_asset_sha256'), table_name='asset')
    op.drop_index(op.f('ix_asset_kind'), table_name='asset')
    op.drop_table('asset')
    # `drop_table` leaves the enum type behind, so without this a downgrade cannot be
    # followed by an upgrade: `CREATE TYPE assetkind` fails with DuplicateObject. The
    # older migrations in this directory have the same gap for `templatekind`,
    # `templatestatus` and `postsource`; this one does not.
    sa.Enum(name='assetkind').drop(op.get_bind())
