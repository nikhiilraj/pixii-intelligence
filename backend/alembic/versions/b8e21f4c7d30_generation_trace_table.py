"""generation_trace table

Revision ID: b8e21f4c7d30
Revises: f3c1d2e4a5b6
Create Date: 2026-08-05 10:20:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = 'b8e21f4c7d30'
down_revision: str | None = 'f3c1d2e4a5b6'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'generation_trace',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('correlation_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('prompt_name', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('prompt_version', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        # Nullable throughout below this line, and every one of them means "not measured"
        # rather than "measured as nothing". `0` tokens would assert a call that cost none.
        sa.Column('model_deployment', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        # `nullable=False` with a server-side default, like `draft.asset_values`: there are no
        # existing rows to backfill, and every reader merges this dict rather than guarding it.
        sa.Column(
            'input_artifact_ids',
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default='{}',
        ),
        sa.Column('output_hash', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('prompt_tokens', sa.Integer(), nullable=True),
        sa.Column('completion_tokens', sa.Integer(), nullable=True),
        sa.Column('retries', sa.Integer(), nullable=True),
        sa.Column('latency_ms', sa.Integer(), nullable=True),
        sa.Column('error', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        # `timestamp without time zone`, like every other datetime column in this schema.
        # Values are written aware and read back naive; `db.utc` is what reconciles them.
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    # The two lookups this table exists to serve: everything one operation did, and every call
    # made under one prompt. There is deliberately no index on `(prompt_name, prompt_version)`
    # as a pair — a prompt has a handful of versions, so the name alone is selective enough.
    op.create_index(
        op.f('ix_generation_trace_correlation_id'),
        'generation_trace',
        ['correlation_id'],
        unique=False,
    )
    op.create_index(
        op.f('ix_generation_trace_prompt_name'), 'generation_trace', ['prompt_name'], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_generation_trace_prompt_name'), table_name='generation_trace')
    op.drop_index(op.f('ix_generation_trace_correlation_id'), table_name='generation_trace')
    op.drop_table('generation_trace')
