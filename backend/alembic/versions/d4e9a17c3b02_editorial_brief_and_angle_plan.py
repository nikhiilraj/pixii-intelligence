"""editorial_brief, angle_plan and planned_claim

Revision ID: d4e9a17c3b02
Revises: c7a5e0b91f24
Create Date: 2026-08-05 17:20:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = 'd4e9a17c3b02'
down_revision: str | None = 'c7a5e0b91f24'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'editorial_brief',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('correlation_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('idea', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('objective', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('audience', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('channel', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('desired_action', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        # `[]` is the honest answer for a brief with no constraints, so not nullable — same
        # treatment as `research_job.mode_signals`.
        sa.Column(
            'constraints',
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default='[]',
        ),
        # Nullable, and it means "the operator expressed no preference". `'none'` here would
        # record a request nobody made — and would make every factual brief raise
        # `ModeBelowFloor`, because `none` is below the floor those briefs require.
        sa.Column('requested_mode', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        # Both, always. One column could not answer "was the recommended depth honoured".
        sa.Column('recommended_mode', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('research_mode', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column(
            'mode_signals',
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default='[]',
        ),
        # NULL is "nobody proposed a time". Never `now()`, which would be the moment the brief
        # was written wearing a publication plan's name.
        sa.Column('proposed_time', sa.DateTime(), nullable=True),
        # The prompt that wrote this row, on the row. Not derivable from `generation_trace`
        # without picking one of several rows sharing the correlation id.
        sa.Column('prompt_name', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('prompt_version', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        # `timestamp without time zone`, like every other datetime column in this schema.
        # Written aware, read back naive; `db.utc` reconciles them.
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_editorial_brief_correlation_id'),
        'editorial_brief',
        ['correlation_id'],
        unique=False,
    )
    op.create_index(
        op.f('ix_editorial_brief_research_mode'), 'editorial_brief', ['research_mode'], unique=False
    )

    op.create_table(
        'angle_plan',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('brief_id', sa.Integer(), nullable=False),
        sa.Column('correlation_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        # One thesis, one column. No second angle and no column that could hold one: a set of
        # angles invites an ordering, and an ordering of angles is a ranking.
        sa.Column('thesis', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('tension', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('audience_stake', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('cta', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column(
            'beats', postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default='[]'
        ),
        # The caller's list, stored verbatim. `[]` means none was given.
        sa.Column(
            'must_not_repeat',
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default='[]',
        ),
        sa.Column('prompt_name', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('prompt_version', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        # No unique constraint on `brief_id`: re-planning writes a new row, so a draft written
        # from the first plan still points at the words it was written from.
        sa.ForeignKeyConstraint(['brief_id'], ['editorial_brief.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_angle_plan_brief_id'), 'angle_plan', ['brief_id'], unique=False)
    op.create_index(
        op.f('ix_angle_plan_correlation_id'), 'angle_plan', ['correlation_id'], unique=False
    )

    op.create_table(
        'planned_claim',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('plan_id', sa.Integer(), nullable=False),
        # One statement per row. A row is what the next slice maps a citation to; a list column
        # would leave "this claim was never sourced" with nothing to point at.
        sa.Column('text', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['plan_id'], ['angle_plan.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_planned_claim_plan_id'), 'planned_claim', ['plan_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_planned_claim_plan_id'), table_name='planned_claim')
    op.drop_table('planned_claim')
    op.drop_index(op.f('ix_angle_plan_correlation_id'), table_name='angle_plan')
    op.drop_index(op.f('ix_angle_plan_brief_id'), table_name='angle_plan')
    op.drop_table('angle_plan')
    op.drop_index(op.f('ix_editorial_brief_research_mode'), table_name='editorial_brief')
    op.drop_index(op.f('ix_editorial_brief_correlation_id'), table_name='editorial_brief')
    op.drop_table('editorial_brief')
