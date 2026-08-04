from datetime import UTC, date, datetime

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel

# The one slot there is. Named rather than assumed so the unique constraint reads as
# "one run per day per slot" and a second slot is a value, not a migration.
DAILY_SLOT = "daily"


class DailyRun(SQLModel, table=True):
    """One editorial run for one local day. The row *is* the lock.

    The scheduler used to fire `run_autonomous` on a 24-hour **interval**, which measures
    from process start: a laptop that restarts at 08:00 every morning never reaches hour 24,
    so the daily run never happened, and two API processes ran it twice. Nothing recorded
    that a run had occurred either, so neither failure was visible from anywhere but the log.

    `UNIQUE (run_date, slot)` plus `INSERT … ON CONFLICT DO NOTHING` replaces both. A tick
    that wins the insert owns the run; every other tick gets nothing back and returns. That
    is the whole locking story — no advisory lock, no leader election, no queue.

    ponytail: one row per day, no state machine. The blueprint's twelve states buy nothing
    while every transition happens inside one process inside one minute. Add them when a
    transition can outlive the call that made it.
    """

    __tablename__ = "daily_run"
    __table_args__ = (UniqueConstraint("run_date", "slot", name="uq_daily_run_date_slot"),)

    id: int | None = Field(default=None, primary_key=True)

    # The **local** date of the slot, not UTC. A 09:00 Asia/Kolkata slot is the previous
    # UTC day; keying on the UTC date would run it twice on some days and not at all on
    # others. See `daily.slot_date`.
    run_date: date = Field(index=True)
    slot: str = Field(default=DAILY_SLOT)

    # "running" | "complete" | "failed". A row stuck on "running" is a process that died
    # mid-run; it is deliberately *not* retried today, because the drafts it had already
    # created are real and a retry would double them.
    status: str = Field(default="running")

    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None

    # Nullable, and that is the point: NULL means the run has not finished, so nothing has
    # been counted yet. `0` would say it finished and produced nothing — the same distinction
    # the metrics columns make, applied to the one place a reader will assume otherwise.
    drafts_created: int | None = None
    topics_failed: int | None = None
    visuals_failed: int | None = None

    error: str | None = None

    # The per-draft messages the run produced, newline-joined. `run_autonomous` names each
    # failure as it happens — an `UnresolvableAsset` is a template to fix, a
    # `MissingSlotValue` is a slot nobody has chosen an asset for — and the counts alone
    # cannot tell those apart. Stored rather than delivered as it happens, because the card
    # is sent once and may be retried hours later from this row alone.
    detail: str | None = None

    # When Teams accepted the card. NULL after a finished run means delivery has not
    # succeeded yet and the next tick should try again — which is what makes the
    # notification retriable without a second table.
    notified_at: datetime | None = None
