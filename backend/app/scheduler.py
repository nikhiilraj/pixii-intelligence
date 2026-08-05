import logging
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler

from app.config import settings
from app.daily import run_daily_slot
from app.db import engine
from app.llm import AzureChat
from app.metrics import sync_metrics
from app.reconcile import reconcile_publications
from app.rendering import AzureImageRenderer, CloudflareRenderer
from app.zernio import ZernioClient

log = logging.getLogger("pixii.scheduler")

# ponytail: APScheduler in the API process. Two periodic jobs do not justify a broker.
# Move to a worker when a job needs isolation or outlives a deploy.
scheduler = BackgroundScheduler(timezone="UTC")


def run_metrics_sync() -> None:
    """Refresh every post's metrics, then reconcile what Zernio accepted but never delivered.

    Failures are logged, never raised into the scheduler.

    Two questions on one tick rather than a second job, and in this order. The sync asks what
    every post has done; reconciliation asks what became of the handful of commands still
    unaccounted for — and it asks **after** the sync has committed, with its own commits, so a
    reconciliation that dies cannot roll back a metrics sync that already succeeded. It also
    means a post the sync just observed live is skipped rather than asked about.

    ponytail: not a third scheduler entry. The note above still holds — two periodic jobs do
    not justify a broker, and a third would only add a way for these to disagree about how
    recent "recent" is.
    """
    from sqlmodel import Session

    client = ZernioClient()
    try:
        with Session(engine) as session:
            result = sync_metrics(session, client)
            session.commit()
            result["reconciled"] = reconcile_publications(session, client)
        log.info("scheduled metrics sync complete: %s", result)
    except Exception:
        # A scheduler that dies on one bad run stops refreshing silently, which is the
        # failure mode this whole system exists to avoid.
        log.exception("scheduled metrics sync failed")
    finally:
        client.close()


def tick_daily_slot() -> None:
    """Ask whether today's editorial slot is due and unclaimed. Usually the answer is no.

    Called far more often than it runs. `run_daily_slot` owns every decision — whether the
    slot has arrived, whether another process already took it, and whether a card still
    needs delivering — because a guard split between the trigger and the function is a guard
    with two versions of the truth.

    The adapters are constructed before that question is asked, which buys three idle HTTP
    clients on most ticks and keeps the `finally` that closes them honest. A run is minutes
    of paid work; the clients are microseconds.

    The image renderer is here because the run's visuals are *suggested*, so a tick cannot
    know whether today's draft needs it — see `generation._renderer_for`. Constructing it
    costs an `httpx.Client` against whatever `azure_openai_image_*` holds, including the
    empty defaults; it reaches the network only when an `ai` template is actually drawn.
    """
    from sqlmodel import Session

    llm, renderer, image_renderer = AzureChat(), CloudflareRenderer(), AzureImageRenderer()
    try:
        with Session(engine) as session:
            run = run_daily_slot(session, llm, renderer, image_renderer)
        if run is not None:
            log.info("daily slot %s finished: %s", run.run_date, run.status)
    except Exception:
        # `run_daily_slot` records and notifies its own failures. This catches what happens
        # around it — a database that will not connect — which has no row to record against.
        log.exception("daily slot tick failed")
    finally:
        llm.close()
        renderer.close()
        image_renderer.close()


def start() -> None:
    if not settings.enable_scheduler:
        log.info("scheduler disabled")
        return
    scheduler.add_job(
        run_metrics_sync,
        "interval",
        hours=settings.metrics_sync_hours,
        id="metrics-sync",
        replace_existing=True,
        # Zernio caches analytics for an hour, so a missed run has nothing to catch up on.
        coalesce=True,
        max_instances=1,
    )
    if settings.enable_autonomous:
        # Here, not on the first tick. `slot_date` builds this on every call inside
        # `tick_daily_slot`'s try, so a typo in `DAILY_SLOT_TIMEZONE` would disable the daily
        # run permanently while `start()` logged the bad string as though it meant something.
        # Failing at boot is the difference between a misconfiguration and a silent outage.
        ZoneInfo(settings.daily_slot_timezone)
        scheduler.add_job(
            tick_daily_slot,
            "interval",
            minutes=settings.daily_tick_minutes,
            id="daily-slot",
            replace_existing=True,
            coalesce=True,
            max_instances=1,
            # Immediately, not one interval from now. A process that starts at 11:00 having
            # been down since 08:00 has a slot to catch up on, and waiting half an hour to
            # ask is half an hour of a run that was already late.
            next_run_time=datetime.now(UTC),
        )
        log.info(
            "daily slot %02d:00 %s, checked every %smin",
            settings.daily_slot_hour,
            settings.daily_slot_timezone,
            settings.daily_tick_minutes,
        )
    else:
        log.info("autonomous generation disabled")

    scheduler.start()
    log.info("scheduler started: metrics sync every %sh", settings.metrics_sync_hours)


def shutdown() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
