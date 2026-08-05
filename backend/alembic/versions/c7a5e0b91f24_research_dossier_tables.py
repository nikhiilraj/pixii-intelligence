"""research_job, research_source, claim and citation

Revision ID: c7a5e0b91f24
Revises: b8e21f4c7d30
Create Date: 2026-08-05 14:05:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = 'c7a5e0b91f24'
down_revision: str | None = 'b8e21f4c7d30'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'research_job',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('question', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('correlation_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        # Both, always. One column could not answer "was the recommended mode honoured".
        sa.Column('recommended_mode', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('mode', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        # `[]` is the honest answer for a brief that tripped no signal, so this is not
        # nullable — same treatment as `generation_trace.input_artifact_ids`.
        sa.Column(
            'mode_signals',
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default='[]',
        ),
        sa.Column('state', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('freshness_days', sa.Integer(), nullable=True),
        # The ceilings as run. Not nullable: a job that was given no budget is not a thing
        # this module can produce, and a NULL here would read as "unlimited".
        sa.Column('max_queries', sa.Integer(), nullable=False),
        sa.Column('max_fetches', sa.Integer(), nullable=False),
        sa.Column('max_seconds', sa.Float(), nullable=False),
        # Nullable throughout below, and every NULL means "that step never ran" rather than
        # "it ran and came to nothing". `0` here is a measurement: a search that returned no
        # results, a fetch loop that kept no page. Writing `0` where nothing ran would state
        # an absence as an observation, which is the `—` versus `0` rule this app prints by.
        sa.Column('queries_run', sa.Integer(), nullable=True),
        sa.Column('sources_found', sa.Integer(), nullable=True),
        sa.Column('sources_fetched', sa.Integer(), nullable=True),
        sa.Column('llm_calls', sa.Integer(), nullable=True),
        # Which ceiling stopped it, or NULL for none of them.
        sa.Column('budget_exhausted', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        # NULL means the claim pass never ran; `[]` means it ran and named nothing open.
        sa.Column('open_questions', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        # `timestamp without time zone`, like every other datetime column in this schema.
        # Written aware, read back naive; `db.utc` reconciles them.
        sa.Column('started_at', sa.DateTime(), nullable=False),
        sa.Column('finished_at', sa.DateTime(), nullable=True),
        sa.Column('error', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_research_job_correlation_id'), 'research_job', ['correlation_id'], unique=False
    )
    op.create_index(op.f('ix_research_job_mode'), 'research_job', ['mode'], unique=False)
    op.create_index(op.f('ix_research_job_state'), 'research_job', ['state'], unique=False)

    op.create_table(
        'research_source',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('job_id', sa.Integer(), nullable=False),
        # The address after redirects, which is the only one a citation may name.
        sa.Column('url', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('requested_url', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('title', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('publisher', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        # NULL on every row today: nothing here reads a publication date, and a filled-in
        # `now()` would be the fetch time wearing a publication date's name.
        sa.Column('published_at', sa.DateTime(), nullable=True),
        sa.Column('fetched_at', sa.DateTime(), nullable=False),
        # NULL on every row today: no provider assigns a tier and nothing computes one.
        sa.Column('trust_tier', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column('content_type', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('content_hash', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.ForeignKeyConstraint(['job_id'], ['research_job.id']),
        sa.PrimaryKeyConstraint('id'),
        # One row per address per job, so `sources_fetched` counts pages rather than attempts.
        sa.UniqueConstraint('job_id', 'url', name='uq_research_source_job_url'),
    )
    op.create_index(
        op.f('ix_research_source_job_id'), 'research_source', ['job_id'], unique=False
    )

    op.create_table(
        'claim',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('job_id', sa.Integer(), nullable=False),
        sa.Column('text', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        # Indexed because "every claim nothing supports" is the query this table exists for.
        sa.Column('status', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['job_id'], ['research_job.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_claim_job_id'), 'claim', ['job_id'], unique=False)
    op.create_index(op.f('ix_claim_status'), 'claim', ['status'], unique=False)

    op.create_table(
        'citation',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('claim_id', sa.Integer(), nullable=False),
        sa.Column('source_id', sa.Integer(), nullable=False),
        sa.Column('stance', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        # A short supporting run of words, never the page.
        sa.Column('span', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('source_content_hash', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['claim_id'], ['claim.id']),
        # The foreign key says the source exists; it cannot say the source belongs to this
        # claim's job. A composite key could, at the cost of carrying `job_id` on both sides
        # of every citation; `research._cite` enforces it instead and
        # `test_research` breaks that check on purpose to prove the enforcement is real.
        sa.ForeignKeyConstraint(['source_id'], ['research_source.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_citation_claim_id'), 'citation', ['claim_id'], unique=False)
    op.create_index(op.f('ix_citation_source_id'), 'citation', ['source_id'], unique=False)
    op.create_index(op.f('ix_citation_stance'), 'citation', ['stance'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_citation_stance'), table_name='citation')
    op.drop_index(op.f('ix_citation_source_id'), table_name='citation')
    op.drop_index(op.f('ix_citation_claim_id'), table_name='citation')
    op.drop_table('citation')
    op.drop_index(op.f('ix_claim_status'), table_name='claim')
    op.drop_index(op.f('ix_claim_job_id'), table_name='claim')
    op.drop_table('claim')
    op.drop_index(op.f('ix_research_source_job_id'), table_name='research_source')
    op.drop_table('research_source')
    op.drop_index(op.f('ix_research_job_state'), table_name='research_job')
    op.drop_index(op.f('ix_research_job_mode'), table_name='research_job')
    op.drop_index(op.f('ix_research_job_correlation_id'), table_name='research_job')
    op.drop_table('research_job')
