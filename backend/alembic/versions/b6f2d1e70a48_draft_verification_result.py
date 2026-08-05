"""record which claim or which words of the idea stand behind each assertion a draft makes

Revision ID: b6f2d1e70a48
Revises: a1c7f2b30e94
Create Date: 2026-08-05 23:55:00.000000

**Nullable, with no backfill and no server default, and that is the whole of the decision.**

Nothing verified the drafts that already exist. `_research_findings` checked the *plan* against
the dossier and never looked at the finished words, and for a `none`-mode draft it checked
nothing at all — which is the defect this column's slice closes. So there is no answer to read
off any existing row, and every value this migration could write would be an assertion rather
than a measurement, exactly as `a1c7f2b30e94` puts it.

`{}` or `{"assertions": []}` would be the tempting default and it is the wrong one: it says
"this draft was checked and asserts nothing", which is a real and different answer from "this
draft was never checked". NULL is the second one, and `Draft.verification_result` says so
beside the column. Studio must show those apart — `CLAUDE.md`'s rule about printing `—` rather
than `0` where data was never collected is the same rule, one column over.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "b6f2d1e70a48"
down_revision: str | None = "a1c7f2b30e94"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "draft",
        sa.Column("verification_result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("draft", "verification_result")
