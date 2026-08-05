from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest
from sqlmodel import select

from app import daily
from app.config import settings
from app.daily import run_daily_slot, slot_date
from app.models.daily_run import DailyRun
from app.models.draft import Draft
from app.notify import card
from tests.test_autonomous import (
    BrokenRenderer,
    FakeImageRenderer,
    FakeLLM,
    FakeRenderer,
    add_post,
    library,
)


class Delivered:
    """A stand-in for Teams that records what it was asked to send.

    `ok=False` is the case worth having: a card Teams refused must leave `notified_at` NULL
    so the next tick tries again. Without a failing double, the retry path is never executed
    and the column reads as decoration.
    """

    def __init__(self, ok: bool = True):
        self.ok = ok
        self.cards: list[dict] = []

    def __call__(self, payload: dict) -> bool:
        self.cards.append(payload)
        return self.ok


@pytest.fixture
def teams(monkeypatch) -> Delivered:
    sent = Delivered()
    monkeypatch.setattr(daily, "deliver", sent)
    # `run_daily_slot` skips the delivery-retry branch when no webhook is configured, and
    # the test environment has none. Setting one is what puts that branch under test.
    monkeypatch.setattr(settings, "teams_webhook_url", "https://example.invalid/hook")
    return sent


@pytest.fixture
def ready(session):
    """A library and a corpus, so `run_autonomous` can actually produce a draft."""
    library(session)
    add_post(session, "p1")
    return session


def runs(session) -> list[DailyRun]:
    return list(session.exec(select(DailyRun)).all())


def drafts(session) -> list[Draft]:
    return list(session.exec(select(Draft)).all())


def at(local: str) -> datetime:
    """A UTC instant expressed as a wall-clock time in the configured slot zone."""
    naive = datetime.fromisoformat(local)
    return naive.replace(tzinfo=ZoneInfo(settings.daily_slot_timezone)).astimezone(UTC)


# --- the slot is a local date, not a UTC one -------------------------------------------


def test_the_slot_is_not_due_before_its_hour():
    assert slot_date(at("2026-08-05 08:59")) is None


def test_the_slot_is_due_from_its_hour_onwards():
    assert slot_date(at("2026-08-05 09:00")) == datetime(2026, 8, 5).date()
    assert slot_date(at("2026-08-05 23:30")) == datetime(2026, 8, 5).date()


def test_the_slot_date_is_local_and_can_differ_from_the_utc_date(monkeypatch):
    """The bug a UTC date would cause, pinned.

    At 09:00 Asia/Kolkata the UTC clock still reads 03:30 of the same day, but the two
    dates part company either side of midnight local: 00:30 local on the 6th is 19:00 UTC
    on the 5th. A run keyed on the UTC date would find the 5th already claimed and skip the
    6th entirely.
    """
    monkeypatch.setattr(settings, "daily_slot_hour", 0)
    instant = at("2026-08-06 00:30")

    assert instant.astimezone(UTC).date() == datetime(2026, 8, 5).date()
    assert slot_date(instant) == datetime(2026, 8, 6).date()


# --- one run per day, whatever the tick does -------------------------------------------


def test_a_due_slot_runs_and_records_what_it_did(ready, teams):
    run = run_daily_slot(
        ready, FakeLLM(), FakeRenderer(), FakeImageRenderer(), now=at("2026-08-05 09:05")
    )

    assert run is not None
    assert run.status == "complete"
    assert run.drafts_created == 1
    assert run.finished_at is not None
    assert len(drafts(ready)) == 1


def test_a_second_tick_the_same_day_does_not_run_again(ready, teams):
    run_daily_slot(
        ready, FakeLLM(), FakeRenderer(), FakeImageRenderer(), now=at("2026-08-05 09:05")
    )
    again = run_daily_slot(
        ready, FakeLLM(), FakeRenderer(), FakeImageRenderer(), now=at("2026-08-05 17:40")
    )

    assert again is None
    assert len(runs(ready)) == 1
    # The real cost of a double run: a second batch of paid drafts in the review queue.
    assert len(drafts(ready)) == 1


def test_the_next_day_runs_again(ready, teams):
    run_daily_slot(
        ready, FakeLLM(), FakeRenderer(), FakeImageRenderer(), now=at("2026-08-05 09:05")
    )
    run_daily_slot(
        ready, FakeLLM(), FakeRenderer(), FakeImageRenderer(), now=at("2026-08-06 09:05")
    )

    assert len(runs(ready)) == 2
    assert len(drafts(ready)) == 2


def test_a_tick_before_the_hour_does_nothing_at_all(ready, teams):
    assert (
        run_daily_slot(
            ready, FakeLLM(), FakeRenderer(), FakeImageRenderer(), now=at("2026-08-05 07:00")
        )
        is None
    )
    assert runs(ready) == []
    assert teams.cards == []


# --- failure is recorded and announced, never silent ------------------------------------


class BrokenLLM:
    def complete_json(self, system: str, user: str, images=()) -> dict:
        raise RuntimeError("provider is down")


def test_a_failed_run_is_recorded_and_still_notified(ready, teams):
    run = run_daily_slot(
        ready, BrokenLLM(), FakeRenderer(), FakeImageRenderer(), now=at("2026-08-05 09:05")
    )

    assert run is not None
    assert run.status == "failed"
    assert "provider is down" in (run.error or "")
    assert len(teams.cards) == 1


def test_a_failed_run_does_not_free_the_day_for_a_retry(ready, teams):
    """A failed run has already spent whatever it spent. Retrying it inside the same day
    is how one outage becomes two batches of drafts."""
    run_daily_slot(
        ready, BrokenLLM(), FakeRenderer(), FakeImageRenderer(), now=at("2026-08-05 09:05")
    )
    again = run_daily_slot(
        ready, FakeLLM(), FakeRenderer(), FakeImageRenderer(), now=at("2026-08-05 10:05")
    )

    assert again is None
    assert len(runs(ready)) == 1


def test_a_run_killed_mid_flight_is_still_reported(ready, teams):
    """The failure the claim itself creates.

    A process killed after claiming the day leaves `status="running"` forever: the claim
    correctly stops a second run, and before `_bury_if_stale` nothing ever sent a card, so
    the day passed with no drafts and no message. That is a scheduled job failing in
    silence — the precise thing this slice exists to end.
    """
    run_daily_slot(
        ready, FakeLLM(), FakeRenderer(), FakeImageRenderer(), now=at("2026-08-05 09:05")
    )
    killed = runs(ready)[0]
    killed.status = "running"
    killed.notified_at = None
    ready.add(killed)
    ready.flush()
    teams.cards.clear()

    run_daily_slot(
        ready, FakeLLM(), FakeRenderer(), FakeImageRenderer(), now=at("2026-08-05 12:00")
    )

    ready.refresh(killed)
    assert killed.status == "failed"
    assert "did not finish" in (killed.error or "")
    assert len(teams.cards) == 1
    # Buried, never re-run: whatever it made before dying is real, and a retry doubles it.
    assert len(drafts(ready)) == 1


def test_a_run_still_in_progress_is_left_alone(ready, teams):
    """Twenty minutes in is a run that is working, not one that died."""
    run_daily_slot(
        ready, FakeLLM(), FakeRenderer(), FakeImageRenderer(), now=at("2026-08-05 09:05")
    )
    working = runs(ready)[0]
    working.status = "running"
    working.notified_at = None
    ready.add(working)
    ready.flush()
    teams.cards.clear()

    run_daily_slot(
        ready, FakeLLM(), FakeRenderer(), FakeImageRenderer(), now=at("2026-08-05 09:25")
    )

    ready.refresh(working)
    assert working.status == "running"
    assert teams.cards == []


# --- delivery is retried until it lands, and never repeated afterwards ------------------


def test_one_card_per_run(ready, teams):
    run_daily_slot(
        ready, FakeLLM(), FakeRenderer(), FakeImageRenderer(), now=at("2026-08-05 09:05")
    )
    run_daily_slot(
        ready, FakeLLM(), FakeRenderer(), FakeImageRenderer(), now=at("2026-08-05 12:05")
    )

    assert len(teams.cards) == 1


def test_a_refused_card_is_retried_on_the_next_tick(ready, monkeypatch):
    refused = Delivered(ok=False)
    monkeypatch.setattr(daily, "deliver", refused)
    monkeypatch.setattr(settings, "teams_webhook_url", "https://example.invalid/hook")

    run = run_daily_slot(
        ready, FakeLLM(), FakeRenderer(), FakeImageRenderer(), now=at("2026-08-05 09:05")
    )
    assert run is not None and run.notified_at is None

    refused.ok = True
    run_daily_slot(
        ready, FakeLLM(), FakeRenderer(), FakeImageRenderer(), now=at("2026-08-05 09:35")
    )

    assert len(refused.cards) == 2
    ready.refresh(run)
    assert run.notified_at is not None
    # The retry delivers the card. It must not deliver a second run.
    assert len(drafts(ready)) == 1


# --- the card says what it knows, and says nothing about what it does not ---------------


def test_the_card_carries_the_counts_and_a_link_out(ready, teams):
    run_daily_slot(
        ready, FakeLLM(), FakeRenderer(), FakeImageRenderer(), now=at("2026-08-05 09:05")
    )

    facts = {f["title"]: f["value"] for f in teams.cards[0]["body"][1]["facts"]}
    assert facts["Drafts created"] == "1"
    assert teams.cards[0]["actions"][0]["url"] == settings.pixii_base_url
    assert teams.cards[0]["actions"][0]["type"] == "Action.OpenUrl"


def test_the_card_names_each_failure_and_not_only_the_count(ready, teams):
    """Why the count alone is not enough.

    `run_autonomous` names each failure as it happens because the count says a redraw is
    needed and the message says whether a redraw could possibly help. Routing its notifier
    to a list dropped those messages from Teams entirely; they belong on the card.
    """
    topics = {"topics": [{"idea": "one"}, {"idea": "two"}]}
    run_daily_slot(
        ready,
        FakeLLM(topics=topics),
        BrokenRenderer(),
        FakeImageRenderer(),
        now=at("2026-08-05 09:05"),
    )

    facts = {f["title"]: f["value"] for f in teams.cards[0]["body"][1]["facts"]}
    assert "no visual" in facts["Detail"]
    assert facts["Drafts without a visual"] == "2"
    # The trailing line is `run_autonomous`'s own summary, restating facts already shown.
    assert "run complete" not in facts["Detail"]


def test_an_uncounted_run_shows_a_dash_and_not_a_zero(session, teams):
    """A run that died before counting has not created zero drafts — nobody counted."""
    run = DailyRun(run_date=datetime(2026, 8, 5).date(), status="failed", error="died")
    session.add(run)
    session.flush()

    daily.notify_run(session, run)

    facts = {f["title"]: f["value"] for f in teams.cards[0]["body"][1]["facts"]}
    assert facts["Drafts created"] == "—"
    assert facts["Drafts without a visual"] == "—"


def test_the_card_is_wrapped_the_way_a_teams_workflow_expects(monkeypatch):
    """The connector shape (`{"text": ...}`) is what stopped working. Pin the replacement."""
    posted: list[dict] = []
    monkeypatch.setattr(settings, "teams_webhook_url", "https://example.invalid/hook")

    class Response:
        def raise_for_status(self) -> None:
            return None

    monkeypatch.setattr(
        "app.notify.httpx.post",
        lambda url, json, timeout: posted.append(json) or Response(),  # type: ignore[func-returns-value]
    )

    from app.notify import deliver

    assert deliver(card("hello", [("a", "b")])) is True
    body = posted[0]
    assert body["type"] == "message"
    attachment = body["attachments"][0]
    assert attachment["contentType"] == "application/vnd.microsoft.card.adaptive"
    assert attachment["content"]["type"] == "AdaptiveCard"
    assert "text" not in body


def test_an_unconfigured_webhook_is_not_a_delivery(monkeypatch):
    """False, not True. Returning True would stamp `notified_at` on a card nobody sent."""
    monkeypatch.setattr(settings, "teams_webhook_url", "")

    from app.notify import deliver

    assert deliver(card("hello", [])) is False
