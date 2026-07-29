#!/usr/bin/env python3
"""Correct the one corpus row that claimed a likes count nobody ever measured.

Run with the backend's own interpreter, from `backend/`, so `app` and the `.env` beside
it resolve:

    cd backend && .venv/bin/python ../scripts/correct_external_creator_metrics.py

The row was added by hand through `/corpus/manual` carrying one figure — 1240 total
reactions — and `add_manual_post` copied that total onto `likes` as well. The breakdown was
never captured, so `/posts?sort=likes` and `metrics.record_snapshots` were reading an
invented observation. The per-metric columns go to zero and `engaged_actions` keeps the
1240, the only figure genuinely known: a confident wrong number misleads more than a zero
with a note beside it. The shortcut that wrote it is gone from `add_manual_post`.

The row also belongs in the inspiration cohort. It is another creator's writing, and both
`extraction.Cohort` and `generation._exemplars` select on `settings.inspiration_account`,
so filed under a one-off username it was reference material that nothing could see and
nothing could refuse.

A data correction, not a migration: one row, no schema change.

Idempotent structurally rather than by a guard — the correction erases the very username the
row is selected by, so a second run matches nothing and prints `corrected=0`.
"""

from __future__ import annotations

from app.config import settings
from app.db import engine
from app.models.post import Post, PostSource
from sqlmodel import Session, select

# The username the row was filed under. A literal, because it is a mistake being cleaned
# up rather than a value the app has any other use for.
STALE_ACCOUNT = "External creator"


def main() -> int:
    with Session(engine) as session:
        rows = list(
            session.exec(
                select(Post).where(
                    Post.account_username == STALE_ACCOUNT,
                    Post.source == PostSource.MANUAL,
                )
            ).all()
        )
        for post in rows:
            # engaged_actions is deliberately left alone: it is the measured total, and it
            # will now exceed the sum of the per-metric columns. That gap is the honest
            # record of what was and was not captured.
            post.likes = 0
            post.comments = 0
            post.shares = 0
            post.saves = 0
            post.account_username = settings.inspiration_account
            session.add(post)
        session.commit()

    print(f"corrected={len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
