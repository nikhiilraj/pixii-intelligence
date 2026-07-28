import logging

from apscheduler.schedulers.background import BackgroundScheduler

from app.config import settings
from app.db import engine
from app.metrics import sync_metrics
from app.zernio import ZernioClient

log = logging.getLogger("pixii.scheduler")

# ponytail: APScheduler in the API process. Two periodic jobs do not justify a broker.
# Move to a worker when a job needs isolation or outlives a deploy.
scheduler = BackgroundScheduler(timezone="UTC")


def run_metrics_sync() -> None:
    """Refresh every post's metrics. Failures are logged, never raised into the scheduler."""
    from sqlmodel import Session

    client = ZernioClient()
    try:
        with Session(engine) as session:
            result = sync_metrics(session, client)
            session.commit()
        log.info("scheduled metrics sync complete: %s", result)
    except Exception:
        # A scheduler that dies on one bad run stops refreshing silently, which is the
        # failure mode this whole system exists to avoid.
        log.exception("scheduled metrics sync failed")
    finally:
        client.close()


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
    scheduler.start()
    log.info("scheduler started: metrics sync every %sh", settings.metrics_sync_hours)


def shutdown() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
