import logging

import httpx

from app.config import settings

log = logging.getLogger("pixii.notify")

# Which card schema the payload declares. Teams renders 1.4; asking for something newer is
# how a card silently arrives as a blank block.
CARD_VERSION = "1.4"


def _envelope(card: dict) -> dict:
    """The card, wrapped the way a Teams Workflow HTTP trigger expects to receive it.

    Not the old `{"text": ...}` shape. Microsoft retired Office 365 connector webhooks
    between 2026-05-18 and 2026-05-22, and this app posted plain text to one — so every
    notification since has been going to a URL that no longer delivers, silently, because
    `deliver` swallows the failure by design. The replacement is a tenant-owned Power
    Automate / Teams Workflow whose HTTP trigger forwards this body to a channel, and it
    wants an attachment envelope rather than a text field.

    The workflow's trigger must be restricted to one identity. The URL is a bearer
    credential in URL clothing — anyone holding it can post into the channel.
    """
    return {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": card,
            }
        ],
    }


def deliver(card: dict) -> bool:
    """Send one card. Returns whether Teams accepted it. Never raises.

    A notifier that can fail its caller turns "the job had a problem" into "the job
    crashed", which is strictly worse. But the caller still needs to know whether the
    message landed, or it cannot decide whether to try again — so the outcome comes back
    as a return value instead of an exception.

    Unconfigured returns False, not True: nothing was delivered, and saying otherwise would
    stamp a delivery timestamp on a message that never left the building.
    """
    if not settings.teams_webhook_url:
        return False
    try:
        response = httpx.post(settings.teams_webhook_url, json=_envelope(card), timeout=15.0)
        response.raise_for_status()
    except Exception:
        log.exception("notification could not be delivered")
        return False
    return True


def card(title: str, facts: list[tuple[str, str]], link: tuple[str, str] | None = None) -> dict:
    """An Adaptive Card: a heading, a fact table, and at most one link out.

    `link` is `(label, url)` and produces an `Action.OpenUrl`. It carries no token and no
    command — it is an ordinary HTTPS link to a Pixii page that authenticates and authorises
    on its own. A notification is not proof of authorisation, so nothing security-sensitive
    may ride in a card.
    """
    body: list[dict] = [
        {"type": "TextBlock", "text": title, "weight": "Bolder", "size": "Medium", "wrap": True}
    ]
    if facts:
        body.append(
            {"type": "FactSet", "facts": [{"title": k, "value": v} for k, v in facts]}
        )
    content: dict = {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard",
        "version": CARD_VERSION,
        "body": body,
    }
    if link:
        label, url = link
        content["actions"] = [{"type": "Action.OpenUrl", "title": label, "url": url}]
    return content


def notify(message: str) -> None:
    """Tell someone. Always logs; also posts to Teams when a webhook is configured.

    Kept for the operational one-liners that have no structure worth a fact table. The
    daily run does not use this — see `daily.notify_run`, which has counts to show and a
    delivery outcome to record.
    """
    log.warning("pixii-intelligence: %s", message)
    deliver(card(message, []))
