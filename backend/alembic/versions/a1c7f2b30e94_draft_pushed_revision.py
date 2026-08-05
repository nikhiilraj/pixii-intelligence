"""record which draft revision Zernio actually holds

Revision ID: a1c7f2b30e94
Revises: 7d9a4c2e6f10
Create Date: 2026-08-05 22:40:00.000000

**Every value this migration writes is an assertion, not a measurement.** Nothing recorded
which revision was pushed before this column existed, so the backfill cannot read the answer
off anything — it can only claim one. That is worth stating because a wrong claim here is
invisible: it would make `distribution.submit`'s drift guard pass for a post whose words
nobody confirmed, which is the one failure the guard exists to catch.

The claim being made is `pushed_revision = revision` for every row that has a
`zernio_post_id`, and NULL for the rest. It was checked against the live database rather than
assumed: both pushed drafts (555 and 48) are at revision 1 and have never been rewritten, so
"the revision Zernio holds is the revision this row is on" is true of both. A row that had
been rewritten since its push would make the claim false, and there is no such row.

NULL for never-pushed drafts is the honest value and not a default: there is no remote post,
so there is no revision it is holding. `submit` reaches the drift check only after `NotPushed`
has already refused, so the NULL is never compared.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a1c7f2b30e94"
down_revision: str | None = "7d9a4c2e6f10"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("draft", sa.Column("pushed_revision", sa.Integer(), nullable=True))
    # Nullable with no server default, so the backfill below is the only thing that writes a
    # value. A `server_default` would silently claim a revision for rows that were never
    # pushed, which is the assertion this migration is careful not to make.
    op.execute(
        "UPDATE draft SET pushed_revision = revision WHERE zernio_post_id IS NOT NULL"
    )


def downgrade() -> None:
    op.drop_column("draft", "pushed_revision")
