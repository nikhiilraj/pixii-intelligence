import uuid
from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.dialects.postgresql import insert
from sqlmodel import Session, col, select

from app.config import settings
from app.models.draft import Draft
from app.models.publication import (
    ACCEPTED,
    ACTIONS,
    CANCEL_SCHEDULE,
    FAILED,
    PUBLISH_NOW,
    REQUESTED,
    SCHEDULE,
    Publication,
)
from app.publishing import media_items_for
from app.zernio import ZernioClient, ZernioRefused, ZernioResponseError


class PublishingDisabled(RuntimeError):
    """The kill switch is off. Nothing external happened."""


class StaleRevision(RuntimeError):
    """The draft changed after the reviewer loaded it. Carries the revision it is now on."""

    def __init__(self, submitted: int, current: int) -> None:
        super().__init__(
            f"this draft is now at revision {current}; the command was confirmed against "
            f"revision {submitted}. Reload and confirm again."
        )
        self.submitted = submitted
        self.current = current


class NotPushed(RuntimeError):
    """There is no remote post to act on yet."""


class CommandRefused(RuntimeError):
    """Zernio declined the command. Carries its own reason."""


def resolve(local: datetime, timezone: str) -> datetime:
    """The instant a wall-clock time in a named zone refers to.

    Naive in, aware out. The caller supplies "09:00 on the 12th" and the name of the place
    that means it; this is the only spot that turns the pair into a moment.

    An unknown zone is re-raised as a `ValueError`. `ZoneInfo` raises
    `ZoneInfoNotFoundError`, which subclasses `KeyError` and not `ValueError` — so the route
    handler's `except ValueError` walked straight past it and `Asia/Kolkta` came back as a
    500. It is a typo in a field the client supplies; it deserves the same 422 as a time in
    the past.
    """
    try:
        zone = ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"{timezone!r} is not an IANA timezone name") from exc
    return local.replace(tzinfo=zone).astimezone(UTC)


def idempotency_key(draft_id: int, revision: int, action: str, when: datetime | None) -> str:
    """A stable name for one logical command.

    Derived, never generated: two submissions of the same command must produce the same
    string, or a retry after a timeout becomes a second schedule. The resolved time is part
    of it, so *changing* the time is a different command rather than a repeat of the old one.

    `uuid5` rather than a hash of a formatted string, for the same reason `publishing`
    uses it: a namespaced UUID cannot collide with an id from anywhere else in the system.
    """
    stamp = when.isoformat() if when else "-"
    name = f"pixii-intelligence/publication/{draft_id}/{revision}/{action}/{stamp}"
    return str(uuid.uuid5(uuid.NAMESPACE_URL, name))


def _record(
    session: Session,
    draft: Draft,
    *,
    action: str,
    revision: int,
    local: datetime | None,
    timezone: str | None,
    when: datetime | None,
) -> tuple[Publication, bool]:
    """Write the command down before anything external happens. Returns (row, is_new).

    `ON CONFLICT DO NOTHING` on `idempotency_key` is the duplicate guard, and it is a
    property of the table rather than of this function: a read-then-insert would race with
    itself under a double-click, which is exactly the input this is defending against.

    Committed rather than flushed, and before the API call, deliberately. The row has to
    outlive a request that dies mid-flight — a command whose response was lost is precisely
    the one where we need to know it was already sent.
    """
    key = idempotency_key(draft.id or 0, revision, action, when)
    statement = (
        insert(Publication)
        .values(
            draft_id=draft.id,
            draft_revision=revision,
            action=action,
            requested_local_time=local,
            timezone=timezone,
            scheduled_utc=when,
            idempotency_key=key,
            state=REQUESTED,
        )
        .on_conflict_do_nothing(index_elements=["idempotency_key"])
        .returning(col(Publication.id))
    )
    created = session.execute(statement).scalar_one_or_none()
    session.commit()
    if created is not None:
        row = session.get(Publication, created)
        assert row is not None
        return row, True

    existing = session.exec(
        select(Publication).where(col(Publication.idempotency_key) == key)
    ).one()
    return existing, False


def _payload(
    session: Session, draft: Draft, client: ZernioClient, *, action: str, when: datetime | None
) -> dict:
    """What the PUT sends.

    `isDraft: False` is what takes the post out of draft state. A `scheduledFor` on its own
    leaves it a draft with a schedule it will never act on — the post reads as scheduled in
    Pixii and is not in Zernio, and nothing surfaces the difference.

    `scheduledFor` carries an explicit UTC offset rather than a bare local time. The
    `timezone` field beside it records intent, but an offset-bearing ISO instant cannot be
    shifted a second time by whichever side does the parsing, and a double-shifted schedule
    is a post that goes out at the wrong hour with no error anywhere.

    **The media is re-uploaded on every command, unconditionally.** `push_draft` records the
    measurement: presign returns a `/temp/` URL that Zernio expires after seven days, and the
    file is copied to permanent storage only when a post publishes. That was harmless while
    publishing was an unbounded human act in Zernio and this app could not influence it.
    Now Pixii schedules, so a date more than a week out would publish text with a dead image.
    Unconditional rather than "re-upload if older than N days": one extra upload per command
    — a rare, deliberate act — removes the arithmetic and the class of bug that hides in it.
    """
    if action == CANCEL_SCHEDULE:
        # Back to a draft. The schedule is what is being withdrawn; the post and its words
        # stay exactly where they are.
        return {"isDraft": True}

    payload: dict[str, object] = {"isDraft": False}
    if action == PUBLISH_NOW:
        payload["publishNow"] = True
    elif when is not None:
        payload["scheduledFor"] = when.isoformat()

    items = media_items_for(session, draft, client, reuse=False)
    if items:
        payload["mediaItems"] = items
    return payload


def submit(
    session: Session,
    draft: Draft,
    client: ZernioClient,
    *,
    action: str,
    revision: int,
    local: datetime | None = None,
    timezone: str | None = None,
) -> Publication:
    """Carry out one human command against a post that already exists in Zernio.

    Every guard runs before anything leaves the building, and in this order:

    1. **The kill switch.** `publishing_enabled` is off by default. It stops external
       commands and nothing else — generation, review and pushing drafts continue — which is
       what makes it usable in an incident rather than a code rollback.
    2. **The revision.** The reviewer confirmed against a specific version of the words. If
       the draft moved since, the command refers to something nobody approved.
    3. **A remote post to act on.** This updates an existing Zernio post; it never creates
       one. Push first.
    4. **A complete instant for a schedule.** A time without a zone is not a moment.

    Then the command is written down and committed *before* the call, so a response lost in
    flight leaves a record that the command was sent. A repeat of the same command finds the
    existing row and returns it without a second external effect.
    """
    if not settings.publishing_enabled:
        raise PublishingDisabled(
            "publishing is disabled. Set PUBLISHING_ENABLED=true to allow schedule, "
            "publish and cancel commands."
        )
    if action not in ACTIONS:
        raise ValueError(f"unknown publication action: {action}")
    if revision != draft.revision:
        raise StaleRevision(revision, draft.revision)
    if not draft.zernio_post_id:
        raise NotPushed(
            f"draft {draft.id} has not been pushed to Zernio, so there is no post to "
            f"{action.replace('_', ' ')}."
        )

    when: datetime | None = None
    if action == SCHEDULE:
        if local is None or not timezone:
            raise ValueError("a schedule needs both a local time and an IANA timezone")
        when = resolve(local, timezone)
        if when <= datetime.now(UTC):
            # Zernio would refuse it, but its refusal arrives as a wall of provider text and
            # only after the media has been re-uploaded. Refusing here says what is wrong.
            raise ValueError(f"{local.isoformat()} {timezone} is in the past")

    publication, is_new = _record(
        session, draft, action=action, revision=revision, local=local, timezone=timezone, when=when
    )
    if not is_new and publication.state != REQUESTED:
        # `accepted` and `failed` are terminal. A repeat of a command that already reached a
        # conclusion is the row, and nothing more — a refusal in particular must never be
        # retried as though it were a timeout.
        return publication

    # Falling through on `requested` is deliberate, and it closes the same hole the daily
    # slot had: a process that died between committing this row and getting an answer would
    # otherwise leave a claim that blocks its own retry forever. The operator would resubmit,
    # get HTTP 200 with `state: "requested"` — which reads as accepted — and the post would
    # never be scheduled, with nothing anywhere to notice.
    #
    # Resending is safe because this is a **PUT**. `create_post` had to be guarded against
    # producing a second post; an update sets the same fields on the same post id, so the
    # second one either changes nothing or is refused. The same derived `idempotency_key`
    # goes out as `x-request-id` regardless, so Zernio's own duplicate window sees it as one
    # command too.
    publication.attempts += 1
    try:
        client.update_post(
            draft.zernio_post_id,
            _payload(session, draft, client, action=action, when=when),
            request_id=publication.idempotency_key,
        )
    except (ZernioRefused, ZernioResponseError) as exc:
        publication.state = FAILED
        publication.last_error = str(exc)[:500]
        session.add(publication)
        session.commit()
        raise CommandRefused(str(exc)) from exc

    publication.state = ACCEPTED
    publication.accepted_at = datetime.now(UTC)
    publication.last_error = None
    session.add(publication)
    session.commit()
    return publication


def scheduled_draft_ids(session: Session) -> set[int]:
    """Drafts whose latest accepted command left them waiting on a clock.

    The Inbox presents "pushed but not live" as a human gate. A scheduled post matches that
    predicate and is waiting on nothing but time, so without this it would sit in a queue of
    things a person is supposed to act on and never leave it.
    """
    rows = session.exec(
        select(Publication.draft_id, Publication.action)
        .where(col(Publication.state) == ACCEPTED)
        .order_by(col(Publication.created_at))
    ).all()
    # Last command per draft wins: a cancel after a schedule puts the draft back in the
    # queue, which is the whole point of cancelling.
    latest: dict[int, str] = {draft_id: action for draft_id, action in rows}
    return {draft_id for draft_id, action in latest.items() if action == SCHEDULE}
