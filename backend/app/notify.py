import logging

import httpx

from app.config import settings

log = logging.getLogger("pixii.notify")


def notify(message: str) -> None:
    """Tell someone. Always logs; also posts to Teams when a webhook is configured.

    Never raises: a notifier that can fail the caller turns "the job had a problem" into
    "the job crashed", which is strictly worse.
    ponytail: Teams webhook only — it is the channel already in use. Add others when one
    is actually wanted.
    """
    log.warning("pixii-intelligence: %s", message)
    if not settings.teams_webhook_url:
        return
    try:
        httpx.post(settings.teams_webhook_url, json={"text": message}, timeout=15.0)
    except Exception:
        log.exception("notification could not be delivered")
