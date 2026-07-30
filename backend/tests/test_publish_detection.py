"""Publish detection: stamping `Draft.went_live_at`, and not trusting the analytics window.

The window is the whole point. `sync_metrics` reads `source.fetch_posts()` — Zernio's 50-row
analytics window — and the live account already holds 11 published posts with no analytics row
at all. Detection keyed to what the sync touched would leave those drafts unstamped forever,
with the Inbox reporting them "awaiting Monte" and no error raised anywhere. Every test here
builds the post outside the window on purpose.

`FakeZernio`, `analytics_post` and `a_draft` come from `test_metrics` — there is no shared
Zernio fixture in this repo. No clock is faked either: timestamps are asserted against the
values that produced them, never against a controlled `now`.
"""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.db import get_session
from app.main import app
from app.metrics import sync_metrics
from app.models.post import Post
from tests.test_metrics import FakeZernio, a_draft, analytics_post

PUBLISHED_AT = datetime(2026, 5, 2, 9, 30, tzinfo=UTC)


def utc_naive(value: datetime | None) -> datetime | None:
    """The wall clock, which is UTC either way.

    `went_live_at` is `timestamp without time zone`, like `pushed_at` and every other datetime
    column in this schema, so a value written aware reads back naive once the row has been
    round-tripped — and whether that has happened yet depends on when the ORM expired the
    object. Asserting on the raw attribute makes a passing test depend on that timing.
    """
    return value.replace(tzinfo=None) if value and value.tzinfo else value


def a_post_outside_the_window(session: Session, late_id: str, *, status: str) -> Post:
    """A post that exists in Postgres and is absent from every analytics payload."""
    post = Post(
        zernio_id=f"analytics-row-{late_id}",
        late_post_id=late_id,
        platform="linkedin",
        content="A post.",
        status=status,
        published_at=PUBLISHED_AT,
    )
    session.add(post)
    session.flush()
    return post


def sync_over(session: Session, *late_ids: str) -> dict:
    """A sync whose analytics window contains exactly these posts, and nothing else."""
    return sync_metrics(session, FakeZernio([analytics_post(i) for i in late_ids]))


# --- the window ---------------------------------------------------------------------------


def test_a_published_post_absent_from_the_window_is_still_detected(session):
    """The false negative the slice exists to close.

    The sync's own payload carries a different post entirely, so anything keyed to the
    touched ids stamps nothing — and reports success while doing it.
    """
    draft, _, _ = a_draft(session, "late-outside")
    a_post_outside_the_window(session, "late-outside", status="published")

    result = sync_over(session, "late-inside")

    assert result["went_live"] == 1
    assert utc_naive(draft.went_live_at) == utc_naive(PUBLISHED_AT)


def test_an_empty_window_does_not_stop_detection(session):
    """An empty analytics response is the sharpest form of the same trap."""
    draft, _, _ = a_draft(session, "late-outside")
    a_post_outside_the_window(session, "late-outside", status="published")

    sync_metrics(session, FakeZernio([]))

    assert utc_naive(draft.went_live_at) == utc_naive(PUBLISHED_AT)


def test_a_pushed_draft_with_no_post_at_all_stays_unstamped(session):
    """Pushed but never published. This is the normal state, not a failure."""
    draft, _, _ = a_draft(session, "late-never-published")

    sync_over(session, "late-inside")

    assert draft.went_live_at is None


def test_a_draft_that_never_left_the_building_is_not_considered(session):
    """No `zernio_post_id` means no join key. Nothing can make it live."""
    draft, _, _ = a_draft(session, None)

    assert sync_over(session, "late-inside")["went_live"] == 0
    assert draft.went_live_at is None


# --- the predicate ------------------------------------------------------------------------


@pytest.mark.parametrize("status", ["partial", "failed", "external", "scheduled", ""])
def test_a_post_that_is_not_published_is_not_treated_as_published(session, status):
    """The predicate is `status == "published"`, not the existence of a post row.

    `partial` and `failed` arrive on the analytics payload; `corpus.py` writes `external` for
    manually added reference rows. Row existence would stamp all of these, claiming a
    publication that never happened — and `went_live_at` is stamped once and never
    reconsidered, so a wrong stamp does not correct itself on the next run.
    """
    late_id = f"late-{status or 'blank'}"
    draft, _, _ = a_draft(session, late_id)
    a_post_outside_the_window(session, late_id, status=status)

    result = sync_over(session, "late-inside")

    assert result["went_live"] == 0
    assert draft.went_live_at is None


# --- idempotency --------------------------------------------------------------------------


def test_stamping_is_idempotent_across_re_runs(session):
    """The sync runs every 6h. A second pass must find nothing left to do."""
    draft, _, _ = a_draft(session, "late-outside")
    a_post_outside_the_window(session, "late-outside", status="published")

    first = sync_over(session, "late-inside")
    second = sync_over(session, "late-inside")

    assert (first["went_live"], second["went_live"]) == (1, 0)
    assert utc_naive(draft.went_live_at) == utc_naive(PUBLISHED_AT)


def test_an_existing_stamp_is_never_moved(session):
    """Pinned to a value no re-derivation would produce, so an overwrite would be visible.

    The post's own `published_at` is a different timestamp; if the pass reconsidered
    already-stamped drafts, this would silently become that one.
    """
    pinned = datetime(2026, 4, 1, 8, 0, tzinfo=UTC)
    draft, _, _ = a_draft(session, "late-outside")
    draft.went_live_at = pinned
    a_post_outside_the_window(session, "late-outside", status="published")
    session.flush()

    result = sync_over(session, "late-inside")

    assert result["went_live"] == 0
    assert utc_naive(draft.went_live_at) == utc_naive(pinned)


def test_a_post_with_no_published_at_is_stamped_at_observation(session):
    """Live, with no timestamp reported. Observation time is the most that can be claimed."""
    draft, _, _ = a_draft(session, "late-outside")
    post = a_post_outside_the_window(session, "late-outside", status="published")
    post.published_at = None
    session.flush()
    before = datetime.now(UTC) - timedelta(seconds=1)

    sync_over(session, "late-inside")

    assert draft.went_live_at is not None
    assert utc_naive(draft.went_live_at) >= utc_naive(before)


# --- the sync's own contract ---------------------------------------------------------------


def test_detection_does_not_commit(session, monkeypatch):
    """`sync_metrics` does not commit — `scheduler.py:29` and `main.py:309` do.

    A commit added here would land a half-finished sync on any later failure, and would break
    the rollback every test in this suite depends on.
    """
    commits = []
    monkeypatch.setattr(type(session), "commit", lambda self: commits.append(1))

    a_draft(session, "late-outside")
    a_post_outside_the_window(session, "late-outside", status="published")
    sync_over(session, "late-inside")

    assert commits == []


def test_metrics_already_observed_are_not_erased_by_a_detection_run(session):
    """The `max(observed, stored)` semantics survive. Zernio reports `shares: 0` on every
    LinkedIn row because it does not measure reposts, so a sync must never lower a count."""
    a_draft(session, "late-1")
    sync_metrics(session, FakeZernio([analytics_post("late-1", likes=40, shares=3)]))
    sync_metrics(session, FakeZernio([analytics_post("late-1", likes=0, shares=0)]))

    post = session.exec(select(Post).where(Post.late_post_id == "late-1")).one()
    assert (post.likes, post.shares) == (40, 3)


# --- the DraftOut trap --------------------------------------------------------------------


@pytest.fixture
def client(session: Session) -> Iterator[TestClient]:
    app.dependency_overrides[get_session] = lambda: session
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_went_live_at_reaches_the_api(client, session):
    """`DraftOut` is hand-mapped in two places (`api_drafts.py:33` and `:72`), so the column
    could exist, be stamped, and still be invisible to the Inbox with everything green.

    Asserted against the serialised HTTP body, because the model is exactly what would still
    be right while the response was wrong.
    """
    draft, _, _ = a_draft(session, "late-outside")
    a_post_outside_the_window(session, "late-outside", status="published")

    assert client.get(f"/drafts/{draft.id}").json()["went_live_at"] is None

    sync_over(session, "late-inside")

    body = client.get(f"/drafts/{draft.id}").json()
    assert body["went_live_at"] is not None
    assert client.get("/drafts").json()[0]["went_live_at"] == body["went_live_at"]
