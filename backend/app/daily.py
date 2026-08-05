import logging
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.dialects.postgresql import insert
from sqlmodel import Session, col, select

from app.autonomous import run_autonomous
from app.config import settings
from app.db import utc
from app.llm import LLM
from app.models.daily_run import DAILY_SLOT, DailyRun
from app.notify import card, deliver
from app.rendering import HtmlRenderer, ImageRenderer

log = logging.getLogger("pixii.daily")

# Shown where a count is not yet known, never `0`. A run that died before generating has
# not created zero drafts — nobody counted. Same rule the corpus and scoreboard follow.
UNKNOWN = "—"

# How long a row may sit on "running" before a later tick declares it dead.
#
# A process killed after it claimed the day leaves `status="running"` and `notified_at`
# NULL forever: the claim stops a second run (right), and nothing ever sends a card
# (wrong) — which is a scheduled job failing in silence, the exact thing this slice exists
# to end. Two hours is well beyond any real run and well inside the day, so the card still
# arrives while it means something.
#
# The row is marked failed but **never re-run**. Whatever drafts it made before dying are
# real, and a retry would double them.
STALE_RUN_HOURS = 2


def slot_date(now: datetime | None = None) -> date | None:
    """The local date whose slot is due, or None if today's slot has not arrived yet.

    Local, not UTC, on purpose: a 09:00 Asia/Kolkata slot falls on the previous UTC day for
    most of its life, so keying the run on `datetime.utcnow().date()` would run it twice on
    the days the boundary moved and skip it on others.

    Returning None before the hour is what makes a frequent tick safe: the tick asks "is the
    slot due", not "is it exactly 09:00", so a process that was asleep at 09:00 still runs
    the day's slot when it wakes at 11:20. That self-healing is the reason this is a check
    against the clock rather than a cron trigger — cron fires once and forgives nothing.
    """
    local = (now or datetime.now(UTC)).astimezone(ZoneInfo(settings.daily_slot_timezone))
    return local.date() if local.hour >= settings.daily_slot_hour else None


def _claim(session: Session, run_date: date, *, at: datetime) -> DailyRun | None:
    """Take ownership of this date's run, or return None because someone already has.

    `ON CONFLICT DO NOTHING` against `UNIQUE (run_date, slot)` is the entire lock. It is
    atomic in one statement, so two processes ticking in the same second cannot both win,
    and it needs no lock table, no lease, and nothing to clean up after a crash.

    Committed immediately rather than flushed. The claim has to be visible to the other
    process *before* this one starts a run that takes minutes — a flush inside an open
    transaction is visible to nobody, which would make the guard look correct and hold
    nothing.
    """
    statement = (
        insert(DailyRun)
        .values(run_date=run_date, slot=DAILY_SLOT, status="running", started_at=at)
        .on_conflict_do_nothing(index_elements=["run_date", "slot"])
        .returning(col(DailyRun.id))
    )
    claimed = session.execute(statement).scalar_one_or_none()
    session.commit()
    if claimed is None:
        return None
    return session.get(DailyRun, claimed)


def _today(session: Session, run_date: date) -> DailyRun | None:
    return session.exec(
        select(DailyRun).where(
            col(DailyRun.run_date) == run_date, col(DailyRun.slot) == DAILY_SLOT
        )
    ).first()


def _bury_if_stale(session: Session, run: DailyRun, *, at: datetime) -> None:
    """Declare a long-`running` row dead, so its card can finally be sent.

    The only way a row stays `running` is a process that died between claiming the day and
    finishing it. Nothing can recover what it was doing — but something has to say so, or
    the day passes with no run and no message.

    Compared through `db.utc` because `started_at` reads back naive once the row has
    round-tripped, and subtracting a naive from an aware datetime raises.
    """
    if at - utc(run.started_at) < timedelta(hours=STALE_RUN_HOURS):
        return
    run.status = "failed"
    run.error = f"run did not finish; no progress for over {STALE_RUN_HOURS}h"
    run.finished_at = at
    session.add(run)
    session.commit()


def notify_run(session: Session, run: DailyRun, *, at: datetime | None = None) -> bool:
    """Post one card for this run, at most once. Returns whether it was sent now.

    `notified_at` is written only after Teams accepts the card, so a delivery that failed
    leaves the column NULL and the next tick tries again. That is the whole retry
    mechanism — a nullable timestamp doing the work of a delivery table.
    """
    if run.notified_at is not None:
        return False

    failed = run.status == "failed"
    count = run.drafts_created
    title = (
        "Pixii daily run failed"
        if failed
        else f"Pixii daily run: {count} draft(s) ready for review"
        if count
        else "Pixii daily run produced no drafts"
    )
    facts = [
        ("Slot", f"{run.run_date} {settings.daily_slot_hour:02d}:00 "
                 f"{settings.daily_slot_timezone}"),
        ("Drafts created", UNKNOWN if count is None else str(count)),
        ("Topics failed", UNKNOWN if run.topics_failed is None else str(run.topics_failed)),
        # Named "without a visual" rather than "visuals failed": the drafts exist and their
        # words are intact, which is a redraw and not a loss.
        (
            "Drafts without a visual",
            UNKNOWN if run.visuals_failed is None else str(run.visuals_failed),
        ),
    ]
    if run.error:
        facts.append(("Error", run.error[:300]))
    if run.detail:
        # The per-draft messages, not only the counts. `autonomous.run_autonomous` names each
        # failure because the count says a redraw is needed and the message says whether a
        # redraw could possibly help — an `UnresolvableAsset` naming a missing default is a
        # template to fix, a `MissingSlotValue` is a slot nobody has chosen an asset for. The
        # old notifier sent one line per draft; folding them into one fact keeps that
        # distinction on the card without turning a daily run into a thread of messages.
        facts.append(("Detail", run.detail[:800]))

    # To the Inbox, not to one draft. A run can produce several, and the Inbox is the screen
    # that puts the oldest waiting item first — which is the question the card is prompting.
    # ponytail: a direct `/studio?draft=N` link when a run makes exactly one draft would be
    # nicer; it needs `RunResult` to carry the ids it currently throws away. Add it then.
    if not deliver(card(title, facts, link=("Open Pixii Inbox", settings.pixii_base_url))):
        return False

    run.notified_at = at or datetime.now(UTC)
    session.add(run)
    session.commit()
    return True


def run_daily_slot(
    session: Session,
    llm: LLM,
    renderer: HtmlRenderer | ImageRenderer,
    *,
    now: datetime | None = None,
) -> DailyRun | None:
    """Run today's slot if it is due and nobody has. Returns the run, or None.

    Safe to call as often as you like — that is the design. Every tick either claims the
    day (at most one wins), retries an undelivered card, or does nothing.

    A run that raises is recorded as `failed` and still notified. The failure this system
    exists to correct is a scheduled job that stops working in silence, so the one outcome
    that must never happen is an exception that leaves no row and sends no card.
    """
    run_date = slot_date(now)
    if run_date is None:
        return None

    # One instant for the whole tick. Reading the clock again at each step would let a run
    # finish before it started under a supplied `now`, and makes the stale-run threshold
    # untestable without sleeping through it.
    at = now or datetime.now(UTC)

    run = _claim(session, run_date, at=at)
    if run is None:
        # Someone else owns today. Their delivery may still have failed, and delivery is
        # the part worth retrying: the drafts are already made either way.
        existing = _today(session, run_date)
        if existing is None or not settings.teams_webhook_url:
            return None
        if existing.status == "running":
            _bury_if_stale(session, existing, at=at)
        if existing.status != "running":
            notify_run(session, existing, at=at)
        return None

    # Collected rather than delivered one by one. `run_autonomous` names each failure as it
    # happens, and the old scheduler forwarded every line straight to Teams — a run with
    # three bad topics was four messages. They ride on the single daily card instead.
    lines: list[str] = []
    try:
        result = run_autonomous(
            session, llm, renderer, cap=settings.autonomous_max_drafts, notify=lines.append
        )
    except Exception as exc:
        # Deliberately broad. Anything that escapes `run_autonomous` — a provider outage, a
        # bug — has to end as a recorded, notified failure rather than as a traceback in a
        # log nobody reads.
        #
        # **No rollback here**, and that is deliberate. `run_autonomous` flushes each draft
        # and never commits, so a rollback would discard every draft the run had already
        # produced before it died — paid work, thrown away to tidy up a session. The two
        # exceptions that actually escape it (no topics proposed, no approved templates)
        # both happen before any draft is written, so there is nothing to tidy. A genuine
        # database error is the case this does not handle: the commit below fails too, the
        # row stays `running`, and the tick logs it — which is honest about a process that
        # died mid-run, and the one state deliberately not retried the same day.
        log.exception("daily run failed")
        run.status = "failed"
        run.error = str(exc)[:500]
    else:
        run.status = "complete"
        run.drafts_created = result.created
        run.topics_failed = result.failed
        run.visuals_failed = result.visuals_failed

    # The trailing line is always `run_autonomous`'s own summary, which restates the counts
    # this card already shows as facts — or, when it raised, the reason already in `error`.
    # Either way it is the one line worth dropping.
    run.detail = "\n".join(lines[:-1]) or None
    run.finished_at = at
    session.add(run)
    session.commit()
    notify_run(session, run, at=at)
    return run
