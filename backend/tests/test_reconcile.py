from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.config import settings
from app.db import utc
from app.distribution import idempotency_key, scheduled_draft_ids
from app.main import inbox
from app.models.publication import (
    ACCEPTED,
    CANCEL_SCHEDULE,
    FAILED,
    PUBLISH_NOW,
    PUBLISHED,
    SCHEDULE,
    Publication,
)
from app.reconcile import reconcile_publications
from app.zernio import ZernioClient
from tests.test_distribution import pushed

NOW = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)


class Zernio:
    """A Zernio that answers one question: what is this post now?

    `gets` records every read. Asserting it is empty is the difference between "the pass
    declined to reconcile this" and "the pass reconciled it and happened to change nothing" —
    a distinction a state assertion alone cannot make, and the shape of the six tests
    `CLAUDE.md` records as passing against routes that did not exist.
    """

    def __init__(self, body: dict | None = None, *, status: int = 200) -> None:
        self.body: dict = {} if body is None else body
        self.status = status
        self.gets: list[str] = []

    def client(self) -> ZernioClient:
        def handler(request: httpx.Request) -> httpx.Response:
            self.gets.append(request.url.path)
            if self.status >= 400:
                return httpx.Response(self.status, text="no such post")
            return httpx.Response(200, json={"post": self.body})

        return ZernioClient(
            api_key="k",
            base_url="https://example.test/api/v1",
            transport=httpx.MockTransport(handler),
        )


class Teams:
    """A Teams that records the cards it was given, and can be told to refuse them."""

    def __init__(self, *, accepts: bool = True) -> None:
        self.accepts = accepts
        self.cards: list[dict] = []

    def deliver(self, card: dict) -> bool:
        self.cards.append(card)
        return self.accepts


@pytest.fixture
def teams(monkeypatch) -> Teams:
    double = Teams()
    monkeypatch.setattr("app.reconcile.deliver", double.deliver)
    return double


@pytest.fixture
def refusing_teams(monkeypatch) -> Teams:
    double = Teams(accepts=False)
    monkeypatch.setattr("app.reconcile.deliver", double.deliver)
    return double


def accepted(
    session,
    draft,
    *,
    action: str = SCHEDULE,
    when: datetime | None = None,
    at: datetime | None = None,
) -> Publication:
    """One command Zernio took, written the way `submit` leaves it.

    Constructed rather than submitted: this suite is about what happens *after* acceptance,
    and going through `submit` would drag the kill switch, a revision and a media round trip
    into every test of a code path that touches none of them.
    """
    row = Publication(
        draft_id=draft.id or 0,
        draft_revision=draft.revision,
        action=action,
        requested_local_time=(when.replace(tzinfo=None) if when else None),
        timezone="Asia/Kolkata" if when else None,
        scheduled_utc=when,
        idempotency_key=idempotency_key(draft.id or 0, draft.revision, action, when),
        state=ACCEPTED,
        attempts=1,
        accepted_at=at or (NOW - timedelta(hours=1)),
    )
    session.add(row)
    session.flush()
    return row


def overdue() -> datetime:
    """A scheduled instant comfortably past the grace period at `NOW`."""
    return NOW - timedelta(hours=2)


# --- nothing is asked before its time ----------------------------------------------------


def test_a_schedule_whose_time_has_not_come_is_not_asked_about(session, teams):
    draft = pushed(session)
    publication = accepted(session, draft, when=NOW + timedelta(days=1))
    zernio = Zernio({"status": "published"})

    reconcile_publications(session, zernio.client(), at=NOW)

    # No GET at all — not merely an unchanged state. A pass that asked and then declined to
    # act on a `published` answer would satisfy the state assertion alone.
    assert zernio.gets == []
    assert publication.state == ACCEPTED
    assert publication.checked_at is None
    assert teams.cards == []


def test_a_post_inside_the_grace_period_is_left_alone(session, teams):
    """One second past a schedule is a queue doing its job, not a drift."""
    draft = pushed(session)
    accepted(session, draft, when=NOW - timedelta(minutes=1))
    zernio = Zernio({"status": "published"})

    reconcile_publications(session, zernio.client(), at=NOW)

    assert zernio.gets == []


def test_a_cancel_names_no_moment_and_is_never_reconciled(session, teams):
    """Nothing is expected to appear on the platform, so there is no outcome to observe."""
    draft = pushed(session)
    accepted(session, draft, action=CANCEL_SCHEDULE)
    zernio = Zernio({"status": "draft"})

    reconcile_publications(session, zernio.client(), at=NOW)

    assert zernio.gets == []


def test_a_draft_already_known_live_is_not_asked_about(session, teams):
    draft = pushed(session)
    draft.went_live_at = NOW - timedelta(hours=1)
    session.flush()
    accepted(session, draft, when=overdue())

    zernio = Zernio({"status": "published"})
    reconcile_publications(session, zernio.client(), at=NOW)

    assert zernio.gets == []


# --- the post went out --------------------------------------------------------------------


def test_a_published_post_is_recorded_with_the_platform_time(session, teams):
    draft = pushed(session)
    publication = accepted(session, draft, when=overdue())
    zernio = Zernio({"status": "published", "publishedAt": "2026-08-05T09:30:00Z"})

    counts = reconcile_publications(session, zernio.client(), at=NOW)

    assert zernio.gets == ["/api/v1/posts/zpost-1"]
    assert publication.state == PUBLISHED
    assert publication.remote_status == "published"
    assert utc(publication.checked_at) == NOW
    assert utc(draft.went_live_at) == datetime(2026, 8, 5, 9, 30, tzinfo=UTC)
    assert counts["published"] == 1
    # Success is not news. The only channel this app has is for things that went wrong.
    assert teams.cards == []


def test_a_published_post_with_no_timestamp_is_stamped_at_observation(session, teams):
    """The most that can honestly be claimed about a post found live with no time on it."""
    draft = pushed(session)
    accepted(session, draft, when=overdue())
    zernio = Zernio({"status": "published"})

    reconcile_publications(session, zernio.client(), at=NOW)

    assert utc(draft.went_live_at) == NOW


def test_a_published_post_does_not_reappear_in_the_human_queue(session, teams):
    """The regression that stamping `went_live_at` here exists to prevent.

    Resolving the publication takes it out of `accepted`, so the Inbox stops treating the
    draft as waiting on a clock. With `went_live_at` still NULL it would land straight back in
    queue 3 — "pushed, waiting on you" — on a post that has already gone out, and it might
    never leave: `stamp_published` needs an analytics row, and 11 published posts in this
    account have none.
    """
    draft = pushed(session)
    accepted(session, draft, when=overdue())
    session.flush()

    reconcile_publications(session, Zernio({"status": "published"}).client(), at=NOW)

    queue = inbox(session).pushed_awaiting_monte
    assert [item.id for item in queue.items] == []
    assert queue.count == 0


def test_a_resolved_schedule_stops_being_counted_as_scheduled(session, teams):
    draft = pushed(session)
    accepted(session, draft, when=overdue())
    assert scheduled_draft_ids(session) == {draft.id}

    reconcile_publications(session, Zernio({"status": "published"}).client(), at=NOW)

    assert scheduled_draft_ids(session) == set()


# --- the post did not go out ---------------------------------------------------------------


def test_a_failed_post_is_recorded_with_zernios_own_reason(session, teams):
    draft = pushed(session)
    publication = accepted(session, draft, when=overdue())
    zernio = Zernio({"status": "failed", "error": "the account token expired"})

    counts = reconcile_publications(session, zernio.client(), at=NOW)

    assert publication.state == FAILED
    assert "the account token expired" in (publication.last_error or "")
    assert draft.went_live_at is None
    assert counts["failed"] == 1


def test_a_post_still_in_draft_after_its_time_never_took_the_schedule(session, teams):
    """The `isDraft` trap, seen from the other end.

    `scheduledFor` without `isDraft: false` leaves an existing draft in draft state holding an
    appointment it will never keep. Pixii would show it scheduled, Zernio a draft, and nothing
    anywhere would report the difference. The status plus the clock is a determinate answer,
    not an ambiguous one.
    """
    draft = pushed(session)
    publication = accepted(session, draft, when=overdue())

    reconcile_publications(session, Zernio({"status": "draft"}).client(), at=NOW)

    assert publication.state == FAILED
    assert "still a draft" in (publication.last_error or "")


def test_a_partial_is_a_failure_not_a_publication(session, teams):
    """At least one target did not publish, and this account has exactly one target."""
    draft = pushed(session)
    publication = accepted(session, draft, when=overdue())

    reconcile_publications(session, Zernio({"status": "partial"}).client(), at=NOW)

    assert publication.state == FAILED
    assert draft.went_live_at is None


def test_a_failure_is_noticed(session, teams):
    draft = pushed(session)
    publication = accepted(session, draft, when=overdue())

    reconcile_publications(session, Zernio({"status": "failed"}).client(), at=NOW)

    [card] = teams.cards
    assert card["body"][0]["text"] == "Pixii publication failed"
    assert utc(publication.failure_notified_at) == NOW


def test_a_card_teams_refuses_is_sent_again_on_the_next_tick(session, refusing_teams, monkeypatch):
    """A terminal row is invisible to the due pass, so the retry needs its own sweep.

    Without it the one channel this app has would drop the message permanently on a single
    refused delivery — a publication failing in silence, which is the failure the whole
    project exists to correct.
    """
    draft = pushed(session)
    publication = accepted(session, draft, when=overdue())
    zernio = Zernio({"status": "failed"})

    reconcile_publications(session, zernio.client(), at=NOW)
    assert publication.failure_notified_at is None
    assert len(refusing_teams.cards) == 1

    accepting = Teams()
    monkeypatch.setattr("app.reconcile.deliver", accepting.deliver)
    # Nothing is asked of Zernio the second time: the row is already terminal.
    later = Zernio({"status": "failed"})
    reconcile_publications(session, later.client(), at=NOW + timedelta(hours=6))

    assert later.gets == []
    assert len(accepting.cards) == 1
    assert utc(publication.failure_notified_at) == NOW + timedelta(hours=6)


def test_a_delivered_card_is_never_sent_twice(session, teams):
    draft = pushed(session)
    accepted(session, draft, when=overdue())

    reconcile_publications(session, Zernio({"status": "failed"}).client(), at=NOW)
    reconcile_publications(
        session, Zernio({"status": "failed"}).client(), at=NOW + timedelta(hours=6)
    )

    assert len(teams.cards) == 1


# --- what Zernio did not say ----------------------------------------------------------------


def test_a_post_still_scheduled_resolves_nothing(session, teams):
    """Late, or moved inside Zernio. Neither is something this pass may rule on."""
    draft = pushed(session)
    publication = accepted(session, draft, when=overdue())

    reconcile_publications(session, Zernio({"status": "scheduled"}).client(), at=NOW)

    assert publication.state == ACCEPTED
    assert publication.remote_status == "scheduled"
    assert utc(publication.checked_at) == NOW
    assert teams.cards == []


def test_an_answer_with_no_status_records_no_status(session, teams):
    """Unreadable is not a status of `unknown`. It is no reading, and NULL says so."""
    draft = pushed(session)
    publication = accepted(session, draft, when=overdue())

    counts = reconcile_publications(session, Zernio({}).client(), at=NOW)

    assert publication.state == ACCEPTED
    assert publication.remote_status is None
    assert counts["unresolved"] == 1


def test_an_unreachable_post_writes_nothing_at_all(session, teams):
    """We did not get an answer, so `checked_at` would claim a reading that never happened."""
    draft = pushed(session)
    publication = accepted(session, draft, when=overdue())

    counts = reconcile_publications(session, Zernio(status=500).client(), at=NOW)

    assert publication.state == ACCEPTED
    assert publication.checked_at is None
    assert counts["checked"] == 0


def test_an_unresolved_command_is_asked_again_next_tick(session, teams):
    """The reason there is no third state: a post that publishes late is still recorded."""
    draft = pushed(session)
    publication = accepted(session, draft, when=overdue())

    reconcile_publications(session, Zernio({"status": "scheduled"}).client(), at=NOW)
    later = Zernio({"status": "published"})
    reconcile_publications(session, later.client(), at=NOW + timedelta(hours=3))

    assert later.gets == ["/api/v1/posts/zpost-1"]
    assert publication.state == PUBLISHED


def test_a_long_unconfirmed_command_is_noticed_without_being_called_failed(session, teams):
    draft = pushed(session)
    publication = accepted(session, draft, when=overdue())
    late = NOW + timedelta(hours=settings.reconcile_stale_hours)

    reconcile_publications(session, Zernio({"status": "scheduled"}).client(), at=late)

    [card] = teams.cards
    assert card["body"][0]["text"] == "Pixii cannot confirm a publication"
    # Noticed, not concluded. The row is still `accepted` and still being polled.
    assert publication.state == ACCEPTED
    assert utc(publication.unconfirmed_notified_at) == late
    assert publication.failure_notified_at is None


def test_the_cannot_confirm_card_does_not_swallow_a_later_failure(session, teams):
    """Two stamps, not one. A failure after an unconfirmed notice is new information."""
    draft = pushed(session)
    publication = accepted(session, draft, when=overdue())
    late = NOW + timedelta(hours=settings.reconcile_stale_hours)

    reconcile_publications(session, Zernio({"status": "scheduled"}).client(), at=late)
    reconcile_publications(
        session, Zernio({"status": "failed"}).client(), at=late + timedelta(hours=6)
    )

    titles = [card["body"][0]["text"] for card in teams.cards]
    assert titles == ["Pixii cannot confirm a publication", "Pixii publication failed"]
    assert publication.state == FAILED


# --- which command a post is living under ---------------------------------------------------


def test_a_cancelled_schedule_is_never_reported_as_failed(session, teams):
    """The false alarm this would otherwise raise on every post its operator withdrew."""
    draft = pushed(session)
    accepted(session, draft, when=overdue())
    accepted(session, draft, action=CANCEL_SCHEDULE, at=NOW - timedelta(minutes=30))
    zernio = Zernio({"status": "draft"})

    reconcile_publications(session, zernio.client(), at=NOW)

    assert zernio.gets == []
    assert teams.cards == []


def test_a_rescheduled_post_is_judged_on_the_later_command_only(session, teams):
    """Two accepted schedules on one post must not produce two cards for one failure."""
    draft = pushed(session)
    superseded = accepted(session, draft, when=overdue())
    current = accepted(
        session, draft, when=NOW + timedelta(days=2), at=NOW - timedelta(minutes=10)
    )
    zernio = Zernio({"status": "draft"})

    reconcile_publications(session, zernio.client(), at=NOW)

    # The current command names a moment that has not arrived, so nothing is asked at all.
    assert zernio.gets == []
    assert superseded.state == ACCEPTED
    assert current.state == ACCEPTED
    assert teams.cards == []


# --- a publish_now is measured from its acceptance -------------------------------------------


def test_a_publish_now_is_timed_from_when_zernio_took_it(session, teams):
    """There is no other candidate: "now" is the whole claim being checked."""
    draft = pushed(session)
    publication = accepted(
        session, draft, action=PUBLISH_NOW, at=NOW - timedelta(hours=1)
    )
    zernio = Zernio({"status": "failed", "error": "platform rejected the media"})

    reconcile_publications(session, zernio.client(), at=NOW)

    assert zernio.gets == ["/api/v1/posts/zpost-1"]
    assert publication.state == FAILED


def test_a_publish_now_accepted_moments_ago_is_left_alone(session, teams):
    draft = pushed(session)
    accepted(session, draft, action=PUBLISH_NOW, at=NOW - timedelta(minutes=2))
    zernio = Zernio({"status": "failed"})

    reconcile_publications(session, zernio.client(), at=NOW)

    assert zernio.gets == []
