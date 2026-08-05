"""claim column so one workflow request cannot buy two runs

Revision ID: c1a4e8d70b39
Revises: b6f2d1e70a48
Create Date: 2026-08-05 00:00:00.000000

**The UNIQUE index is the guard; the column is only where it lives.** `POST /drafts/workflow`
now returns before the run finishes, so a double-click or a refresh sends the same request
twice within milliseconds of each other, and a read-then-insert would find nothing both times.
`distribution._record` closes the same race the same way and its comment explains why nothing
in Python can: two statements are two chances to interleave, one is not.

NULL is "claiming nothing", and Postgres permits any number of NULLs under a unique index — so
a finished run releases its claim by writing NULL and needs no partial index and no sentinel.
That is also why nothing is backfilled here: every existing draft has already finished, so
every one of them is claiming nothing, which is exactly what the NULL this adds says.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c1a4e8d70b39"
down_revision: str | None = "b6f2d1e70a48"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("draft", sa.Column("workflow_key", sa.String(), nullable=True))
    # A UNIQUE *constraint*, not a unique index, because `Field(unique=True)` declares one —
    # and `alembic check` compares the two as different objects. They enforce the same thing;
    # only one of them makes the model and the migration agree.
    op.create_unique_constraint(None, "draft", ["workflow_key"])


def downgrade() -> None:
    op.drop_constraint("draft_workflow_key_key", "draft", type_="unique")
    op.drop_column("draft", "workflow_key")
