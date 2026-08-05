"""connect drafts to editorial, research and review artifacts

Revision ID: 7d9a4c2e6f10
Revises: d4e9a17c3b02
Create Date: 2026-08-05 20:15:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "7d9a4c2e6f10"
down_revision: str | None = "d4e9a17c3b02"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("draft", sa.Column("editorial_brief_id", sa.Integer(), nullable=True))
    op.add_column("draft", sa.Column("angle_plan_id", sa.Integer(), nullable=True))
    op.add_column("draft", sa.Column("research_job_id", sa.Integer(), nullable=True))
    op.add_column(
        "draft", sa.Column("correlation_id", sqlmodel.sql.sqltypes.AutoString(), nullable=True)
    )
    op.add_column(
        "draft",
        sa.Column(
            "generation_stage",
            sqlmodel.sql.sqltypes.AutoString(),
            nullable=False,
            server_default="ready",
        ),
    )
    op.add_column(
        "draft", sa.Column("generation_error", sqlmodel.sql.sqltypes.AutoString(), nullable=True)
    )
    op.add_column(
        "draft",
        sa.Column(
            "gate_results",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
    )
    op.add_column(
        "draft",
        sa.Column("readiness_result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "draft",
        sa.Column("revision_rounds", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "draft", sa.Column("write_prompt_name", sqlmodel.sql.sqltypes.AutoString(), nullable=True)
    )
    op.add_column(
        "draft",
        sa.Column("write_prompt_version", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    )

    op.create_foreign_key(None, "draft", "editorial_brief", ["editorial_brief_id"], ["id"])
    op.create_foreign_key(None, "draft", "angle_plan", ["angle_plan_id"], ["id"])
    op.create_foreign_key(None, "draft", "research_job", ["research_job_id"], ["id"])
    indexed = (
        "editorial_brief_id",
        "angle_plan_id",
        "research_job_id",
        "correlation_id",
        "generation_stage",
    )
    for column in indexed:
        op.create_index(op.f(f"ix_draft_{column}"), "draft", [column], unique=False)


def downgrade() -> None:
    indexed = (
        "editorial_brief_id",
        "angle_plan_id",
        "research_job_id",
        "correlation_id",
        "generation_stage",
    )
    for column in reversed(indexed):
        op.drop_index(op.f(f"ix_draft_{column}"), table_name="draft")
    for column in reversed(
        (
            "editorial_brief_id",
            "angle_plan_id",
            "research_job_id",
            "correlation_id",
            "generation_stage",
            "generation_error",
            "gate_results",
            "readiness_result",
            "revision_rounds",
            "write_prompt_name",
            "write_prompt_version",
        )
    ):
        op.drop_column("draft", column)
