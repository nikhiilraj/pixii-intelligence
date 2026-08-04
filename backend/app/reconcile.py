import logging
from datetime import UTC, datetime, timedelta

from sqlmodel import Session, col, select

from app.config import settings
from app.db import utc
from app.distribution import latest_accepted
from app.models.draft import Draft
from app.models.publication import (
    FAILED,
    PUBLISH_NOW,
    PUBLISHED,
    SCHEDULE,
    Publication,
)
from app.notify import card, deliver
from app.zernio import ZernioClient

log = logging.getLogger("pixii.reconcile")

# Shown where nothing was measured, never `0` and never an empty cell. Same rule as
# `daily.UNKNOWN`, which explains it at length: a count nobody took is not a count of zero.
UNKNOWN = "—"

# The words Zernio uses for a post, lowercased. `published` and `failed` are the only two
# that answer the question this pass asks; the rest are here so a reader can see that they
# were considered and deliberately do not resolve anything.
#
# `partial` is treated as a failure. It means at least one platform target did not publish,
# and this account has exactly one connected target — so a `partial` is either the only
# target failing under a friendlier name, or a shape nobody here understands. Both need a
# person. ponytail: the day a draft goes to two platforms, this needs the per-target array
# rather than the summary word, and "some of it went out" becomes a real third answer.
_PUBLISHED = "published"
_FAILED = ("failed", "partial")
_STILL_A_DRAFT = "draft"


def _moment(publication: Publication) -> datetime | None:
    """When this command was supposed to have taken effect, or None if it names no moment.

    A schedule points at its own instant. A `publish_now` points at the moment Zernio said
    yes — there is no other candidate, and "now" is the whole claim being checked.

    `cancel_schedule` returns None and is never reconciled. Nothing is expected to appear on
    the platform, so there is no outcome to observe; the acceptance *is* the whole event.
    """
    if publication.action == SCHEDULE:
        return publication.scheduled_utc
    if publication.action == PUBLISH_NOW:
        return publication.accepted_at
    return None


def _due(session: Session, *, at: datetime) -> list[tuple[Publication, Draft]]:
    """The commands whose moment has passed and whose post is not known to be live.

    Only the **latest** accepted command per draft, which is what `latest_accepted` is for. A
    superseded one is not the command the post is living under: a schedule the operator
    cancelled would otherwise be found unfired and reported as a failure, and a reschedule
    would produce two cards for one post.

    The moment is compared in Python rather than in the WHERE clause. Every datetime column
    here is `timestamp without time zone`, so `scheduled_utc` and `accepted_at` come back
    naive and an aware bound in SQL raises rather than comparing wrongly — the same hazard
    `daily._bury_if_stale` normalises through `db.utc`.
    """
    grace = timedelta(minutes=settings.reconcile_grace_minutes)
    due = []
    for publication in latest_accepted(session).values():
        moment = _moment(publication)
        if moment is None or at - utc(moment) < grace:
            continue
        draft = session.get(Draft, publication.draft_id)
        # Already observed live by the metrics sync, so there is nothing left to ask, and no
        # remote post id means there is nothing to ask *about* — `submit` cannot produce
        # either state, and a GET against `None` would be a 404 read as a failure.
        if draft is None or draft.went_live_at is not None or not draft.zernio_post_id:
            continue
        due.append((publication, draft))
    return due


def _owed_a_card(session: Session) -> list[tuple[Publication, Draft]]:
    """Failures this pass resolved on an earlier tick and could not get a card out for.

    Without this the retry is a lie. A resolved row is no longer `accepted`, so `_due` never
    looks at it again — a card Teams refused once would be lost permanently, and a publication
    that failed in silence is the exact thing this pass exists to prevent.

    `checked_at IS NOT NULL` is what keeps it to reconciliation's own findings. `submit`
    writes `failed` too, when Zernio refuses a command outright — but that refusal is raised
    to the person who pressed the button, in the HTTP response, as it happens. Only this
    module writes `checked_at`, so it is the marker for "nobody was standing there".
    """
    rows = session.exec(
        select(Publication).where(
            col(Publication.state) == FAILED,
            col(Publication.checked_at).is_not(None),
            col(Publication.failure_notified_at).is_(None),
        )
    ).all()
    owed = []
    for publication in rows:
        draft = session.get(Draft, publication.draft_id)
        if draft is not None:
            owed.append((publication, draft))
    return owed


def _published_at(remote: dict) -> datetime | None:
    """The platform's own publication time, if the answer carries a readable one.

    Returns None rather than a guess when the field is absent or unparseable, so the caller
    falls back to the moment of observation — which is the most that can honestly be claimed
    about a post found live with no timestamp on it. Same reasoning as `metrics.stamp_published`.
    """
    for key in ("publishedAt", "published_at"):
        raw = remote.get(key)
        if not isinstance(raw, str) or not raw:
            continue
        try:
            # `Z` is valid ISO 8601 and `fromisoformat` rejected it until recently; the
            # replacement costs nothing and removes a version dependency from a parse that
            # would otherwise silently fall through to "no timestamp".
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
        # Naive means UTC here, not local: assuming the process timezone would move a
        # publication time by hours on the machine this runs on.
        return utc(parsed).astimezone(UTC)
    return None


def _outcome(publication: Publication, remote: dict) -> tuple[str, str | None] | None:
    """What Zernio's answer says happened: `(state, reason)`, or None because it does not say.

    **None is the honest answer far more often than it looks.** An unclear response is not a
    failure and is certainly not a publication; it leaves the row `accepted` to be asked again
    on the next tick. Inventing an outcome here would either mark a healthy post failed or —
    worse — record a post as published on the strength of a response nobody could read.

    Two answers resolve it:

    * `published` — the post went out. This is the only thing that may stamp `went_live_at`.
    * `failed` or `partial` — it did not, and Zernio says so in its own words.

    And one more, which is the trap `update_post` is written around. A post still sitting in
    **draft** whose moment has passed did not acquire a schedule at all: `scheduledFor` alone
    leaves an existing draft in draft state, holding an appointment it will never keep. Pixii
    would show it scheduled, Zernio would show a draft, and nothing anywhere would report the
    difference. That is a determinate answer — two clear signals, the status and the clock —
    not an ambiguous one, so it resolves to `failed`.

    `scheduled` deliberately resolves nothing. The post is still waiting on a queue, whether
    because Zernio is running late or because someone moved the time inside Zernio, and
    neither is something this pass may rule on.
    """
    status = remote.get("status")
    status = status.lower() if isinstance(status, str) else ""

    if status == _PUBLISHED:
        return PUBLISHED, None
    if status in _FAILED:
        # Zernio's own reason when it gives one. `last_error` has meant "the service's words"
        # since `submit` wrote it, and this keeps that true wherever the service supplies them.
        reason = remote.get("error") or remote.get("failureReason")
        return FAILED, (
            f"Zernio reports the post as {status}: {str(reason)[:300]}"
            if reason
            else f"Zernio reports the post as {status}, with no reason given."
        )
    if status == _STILL_A_DRAFT or remote.get("isDraft") is True:
        # These words are **ours**, not Zernio's — the one place `last_error` carries Pixii's
        # own conclusion. Said plainly so nobody reads it as a quoted API message.
        return FAILED, (
            f"Pixii: the {publication.action.replace('_', ' ')} command was accepted, but the "
            f"post is still a draft in Zernio and its time has passed. It was never scheduled."
        )
    return None


def _facts(publication: Publication, draft: Draft) -> list[tuple[str, str]]:
    """The fact table both cards share. Every unmeasured cell prints `—`, never a zero."""
    when = (
        f"{publication.requested_local_time.isoformat(sep=' ', timespec='minutes')} "
        f"{publication.timezone}"
        if publication.requested_local_time and publication.timezone
        else UNKNOWN
    )
    return [
        ("Draft", f"#{draft.id} {(draft.hook_text or draft.idea or '')[:120]}".strip()),
        ("Command", publication.action.replace("_", " ")),
        ("Requested for", when),
        ("Zernio post", draft.zernio_post_id or UNKNOWN),
        ("Zernio status", publication.remote_status or UNKNOWN),
    ]


def _notify_failure(
    session: Session, publication: Publication, draft: Draft, *, at: datetime
) -> bool:
    """Say that a publication failed. Returns whether the card went out on this call.

    `failure_notified_at` is written only after Teams accepts the card, so a refused delivery
    leaves it NULL and the next tick tries again. A publication that failed in silence is the
    exact failure this whole project exists to correct, so the one outcome that must not
    happen is a terminal failure with no card and no record of why there is none.
    """
    if publication.failure_notified_at is not None:
        return False
    facts = _facts(publication, draft)
    facts.append(("Reason", (publication.last_error or UNKNOWN)[:300]))
    if not deliver(
        card("Pixii publication failed", facts, link=("Open Pixii Inbox", settings.pixii_base_url))
    ):
        return False
    publication.failure_notified_at = at
    session.add(publication)
    session.commit()
    return True


def _notify_unconfirmed(
    session: Session, publication: Publication, draft: Draft, *, at: datetime
) -> bool:
    """Say that Pixii cannot tell what happened. Returns whether the card went out.

    Its own stamp, and its own words: this card claims nothing about the post beyond that
    nobody here knows. The row stays `accepted` and keeps being polled, so a post that
    publishes an hour after this card is sent is still recorded correctly — and if the answer
    eventually comes back `failed`, `_notify_failure` sends its own card on its own stamp,
    because that is new information rather than a repeat of this one.
    """
    if publication.unconfirmed_notified_at is not None:
        return False
    facts = _facts(publication, draft)
    facts.append(
        (
            "Unconfirmed for",
            f"over {settings.reconcile_stale_hours}h since the time it was due",
        )
    )
    if not deliver(
        card(
            "Pixii cannot confirm a publication",
            facts,
            link=("Open Pixii Inbox", settings.pixii_base_url),
        )
    ):
        return False
    publication.unconfirmed_notified_at = at
    session.add(publication)
    session.commit()
    return True


def reconcile_publications(
    session: Session, client: ZernioClient, *, at: datetime | None = None
) -> dict:
    """Ask Zernio what became of every command it accepted and nobody has confirmed.

    An `accepted` publication records that Zernio **took the command**, not that the post went
    out. Two drifts live in that gap and neither raises anything: a scheduled post whose time
    passed and which never went live, and a `publish_now` accepted and then not delivered.
    `metrics.stamp_published` does not close either — it observes posts that *did* publish, so
    a post that failed simply never appears, and an absence with no error attached is the
    failure this system exists to end.

    Deliberately does **not** consult `publishing_enabled`. That switch stops external
    *commands*; this sends none. A GET changes nothing on the account, and switching it off
    during an incident is precisely when knowing what happened to an in-flight schedule
    matters most — a reconciler that went quiet with it would hide the incident it was called
    for.

    ponytail: polling on the metrics tick, not a webhook and not a second scheduler job.
    Webhooks need a public HTTPS URL and HMAC verification and there is no public URL; a
    second periodic job does not justify a broker, which is the note `scheduler.py` already
    carries. The ceiling is latency — up to one metrics interval between the truth and Pixii
    knowing it — and the upgrade is Zernio's `post.published` / `post.failed` events the day a
    URL exists to receive them.
    """
    at = at or datetime.now(UTC)
    stale = timedelta(hours=settings.reconcile_stale_hours)
    counts = {"checked": 0, "published": 0, "failed": 0, "unresolved": 0, "notified": 0}

    # Before anything is asked: the cards an earlier tick could not deliver. Their rows are
    # terminal, so nothing else in this pass will ever look at them again.
    for publication, draft in _owed_a_card(session):
        counts["notified"] += _notify_failure(session, publication, draft, at=at)

    for publication, draft in _due(session, at=at):
        try:
            remote = client.get_post(draft.zernio_post_id or "")
        except Exception:
            # One unreachable post must not stop the pass. Nothing is written: we did not get
            # an answer, so `checked_at` would claim a reading that never happened, and the
            # row stays exactly as unresolved as it was.
            log.exception("could not read Zernio post %s", draft.zernio_post_id)
            continue

        counts["checked"] += 1
        publication.checked_at = at
        status = remote.get("status")
        publication.remote_status = status if isinstance(status, str) and status else None

        outcome = _outcome(publication, remote)
        if outcome is None:
            # Unresolved, and stays `accepted` so the next tick asks again. Only the card is
            # decided here, and only once the wait has gone on long enough to be news.
            session.add(publication)
            session.commit()
            counts["unresolved"] += 1
            moment = _moment(publication)
            if moment is not None and at - utc(moment) >= stale:
                counts["notified"] += _notify_unconfirmed(session, publication, draft, at=at)
            continue

        publication.state, publication.last_error = outcome
        if publication.state == PUBLISHED:
            # **The one place reconciliation touches the draft.** `stamp_published` remains
            # the detector that scans the corpus; this is a second *observation*, and a
            # stronger one — a direct read of the post beats the 50-row analytics window that
            # 11 published posts in this account are missing from entirely.
            #
            # Not stamping here would be a regression, not restraint: the row leaves
            # `accepted`, so `scheduled_draft_ids` drops it, and with `went_live_at` still
            # NULL the draft would reappear in Inbox queue 3 as "waiting on you" — on a post
            # that has already gone out — and might never leave, because the analytics window
            # may never carry it.
            draft.went_live_at = _published_at(remote) or at
            session.add(draft)
            counts["published"] += 1
        else:
            counts["failed"] += 1

        session.add(publication)
        session.commit()
        if publication.state == FAILED:
            counts["notified"] += _notify_failure(session, publication, draft, at=at)

    if counts["checked"]:
        log.info("publication reconciliation: %s", counts)
    return counts
