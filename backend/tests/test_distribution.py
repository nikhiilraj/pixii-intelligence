import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from app.config import settings
from app.db import get_session
from app.deps import get_html_renderer, get_image_renderer, get_llm, get_zernio
from app.distribution import (
    CommandRefused,
    NotPushed,
    PublishingDisabled,
    StaleRevision,
    idempotency_key,
    resolve,
    scheduled_draft_ids,
    submit,
)
from app.main import app
from app.models.publication import (
    ACCEPTED,
    CANCEL_SCHEDULE,
    FAILED,
    PUBLISH_NOW,
    SCHEDULE,
    Publication,
)
from app.publishing import push_draft
from app.zernio import ZernioClient
from tests.test_autonomous import FakeLLM, FakeRenderer
from tests.test_publishing import a_draft


class Zernio:
    """A Zernio that records what it was asked, and can be told to refuse.

    Handles the whole media round trip as well as the post update, because a schedule
    re-uploads the visual before it sends anything — a double that only answered the PUT
    would let a broken upload path pass.
    """

    def __init__(self, *, status: int = 200) -> None:
        self.status = status
        self.puts: list[dict] = []
        self.uploads = 0

    def client(self) -> ZernioClient:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/media/presign"):
                self.uploads += 1
                return httpx.Response(
                    200,
                    json={
                        "uploadUrl": f"https://store.test/put/{self.uploads}",
                        # A different URL each time, as presign really behaves. It is what
                        # makes "was the media re-uploaded" an observable fact.
                        "publicUrl": f"https://media.test/temp/{self.uploads}.png",
                    },
                )
            if request.url.host == "store.test":
                return httpx.Response(200)
            self.puts.append(
                {
                    "method": request.method,
                    "path": request.url.path,
                    "body": json.loads(request.content) if request.content else {},
                    "request_id": request.headers.get("x-request-id"),
                }
            )
            if self.status >= 400:
                return httpx.Response(self.status, text="that account cannot post today")
            return httpx.Response(200, json={"post": {"_id": "zpost-1"}})

        return ZernioClient(
            api_key="k",
            base_url="https://example.test/api/v1",
            transport=httpx.MockTransport(handler),
        )


@pytest.fixture
def enabled(monkeypatch):
    """Publishing turned on. Off is the default, so every test here says so explicitly."""
    monkeypatch.setattr(settings, "publishing_enabled", True)


@pytest.fixture
def zernio_double() -> Zernio:
    return Zernio()


@pytest.fixture
def refusing(zernio_double: Zernio) -> Zernio:
    zernio_double.status = 422
    return zernio_double


@pytest.fixture
def api(session, zernio_double: Zernio) -> Iterator[TestClient]:
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_zernio] = zernio_double.client
    # A renderer that always works, so `regenerate-visual` reaches the branch under test
    # rather than the error path.
    app.dependency_overrides[get_llm] = FakeLLM
    app.dependency_overrides[get_html_renderer] = FakeRenderer
    app.dependency_overrides[get_image_renderer] = FakeRenderer
    yield TestClient(app)
    app.dependency_overrides.clear()


def pushed(session):
    draft = a_draft(session)
    draft.zernio_post_id = "zpost-1"
    draft.visual_image = b"IMG"
    session.add(draft)
    session.flush()
    return draft


def soon() -> datetime:
    return (datetime.now(UTC) + timedelta(days=3)).replace(tzinfo=None, microsecond=0)


def rows(session) -> list[Publication]:
    return list(session.exec(select(Publication)).all())


# --- the payload that actually takes a post out of draft state --------------------------


def test_a_schedule_takes_the_post_out_of_draft(session, enabled):
    """The trap this whole slice is built around.

    `scheduledFor` on its own leaves an existing draft *in draft state*. It acquires an
    appointment it will never keep: Pixii shows it scheduled, Zernio shows a draft, and
    nothing anywhere reports a difference. `isDraft: false` is what moves it.
    """
    zernio = Zernio()
    submit(
        session,
        pushed(session),
        zernio.client(),
        action=SCHEDULE,
        revision=1,
        local=soon(),
        timezone="Asia/Kolkata",
    )

    body = zernio.puts[0]["body"]
    assert body["isDraft"] is False
    assert "scheduledFor" in body
    assert zernio.puts[0]["method"] == "PUT"


def test_the_scheduled_time_carries_an_explicit_offset(session, enabled):
    """An offset-bearing instant cannot be shifted a second time by whoever parses it.

    A bare local string plus a `timezone` field is the shape that double-shifts, and a
    double-shifted schedule publishes at the wrong hour with no error anywhere.
    """
    zernio = Zernio()
    submit(
        session,
        pushed(session),
        zernio.client(),
        action=SCHEDULE,
        revision=1,
        local=soon(),
        timezone="Asia/Kolkata",
    )

    sent = zernio.puts[0]["body"]["scheduledFor"]
    assert datetime.fromisoformat(sent).tzinfo is not None


def test_publish_now_says_so(session, enabled):
    zernio = Zernio()
    submit(session, pushed(session), zernio.client(), action=PUBLISH_NOW, revision=1)

    body = zernio.puts[0]["body"]
    assert body["isDraft"] is False
    assert body["publishNow"] is True


def test_cancelling_returns_the_post_to_a_draft(session, enabled):
    zernio = Zernio()
    submit(session, pushed(session), zernio.client(), action=CANCEL_SCHEDULE, revision=1)

    body = zernio.puts[0]["body"]
    assert body["isDraft"] is True
    assert "scheduledFor" not in body
    assert "publishNow" not in body


def test_a_local_time_resolves_through_its_zone():
    assert resolve(datetime(2026, 8, 12, 9, 0), "Asia/Kolkata") == datetime(
        2026, 8, 12, 3, 30, tzinfo=UTC
    )


# --- the guards, each refusing before anything leaves the building -----------------------


def test_the_kill_switch_stops_the_command_and_sends_nothing(session, monkeypatch):
    monkeypatch.setattr(settings, "publishing_enabled", False)
    zernio = Zernio()

    with pytest.raises(PublishingDisabled):
        submit(session, pushed(session), zernio.client(), action=PUBLISH_NOW, revision=1)

    assert zernio.puts == []
    assert rows(session) == []


def test_a_stale_revision_is_refused_and_says_what_is_current(session, enabled):
    """The reviewer confirmed one version of the words; the draft moved since."""
    draft = pushed(session)
    draft.edited()
    draft.edited()
    session.flush()
    zernio = Zernio()

    with pytest.raises(StaleRevision) as caught:
        submit(session, draft, zernio.client(), action=PUBLISH_NOW, revision=1)

    assert caught.value.current == 3
    assert zernio.puts == []


def test_a_draft_that_was_never_pushed_has_nothing_to_command(session, enabled):
    draft = a_draft(session)
    zernio = Zernio()

    with pytest.raises(NotPushed):
        submit(session, draft, zernio.client(), action=PUBLISH_NOW, revision=1)

    assert zernio.puts == []


def test_a_schedule_in_the_past_is_refused_here_not_by_zernio(session, enabled):
    zernio = Zernio()
    past = (datetime.now(UTC) - timedelta(hours=1)).replace(tzinfo=None)

    with pytest.raises(ValueError, match="in the past"):
        submit(
            session,
            pushed(session),
            zernio.client(),
            action=SCHEDULE,
            revision=1,
            local=past,
            timezone="Asia/Kolkata",
        )

    # Refused before the media upload, so a bad time costs nothing.
    assert zernio.uploads == 0


def test_a_time_without_a_zone_is_not_a_moment(session, enabled):
    with pytest.raises(ValueError, match="IANA timezone"):
        submit(
            session,
            pushed(session),
            Zernio().client(),
            action=SCHEDULE,
            revision=1,
            local=soon(),
            timezone=None,
        )


# --- one command, one external effect ---------------------------------------------------


def test_the_same_command_twice_is_one_post(session, enabled):
    """The double-click, and the retry after a lost response. Same thing to this code."""
    draft = pushed(session)
    zernio = Zernio()
    client = zernio.client()
    when = soon()

    first = submit(
        session, draft, client, action=SCHEDULE, revision=1, local=when, timezone="Asia/Kolkata"
    )
    second = submit(
        session, draft, client, action=SCHEDULE, revision=1, local=when, timezone="Asia/Kolkata"
    )

    assert first.id == second.id
    assert len(rows(session)) == 1
    assert len(zernio.puts) == 1


def test_a_different_time_is_a_different_command(session, enabled):
    """Rescheduling must not be mistaken for a repeat of the schedule it replaces."""
    draft = pushed(session)
    zernio = Zernio()
    client = zernio.client()

    submit(
        session, draft, client, action=SCHEDULE, revision=1, local=soon(), timezone="Asia/Kolkata"
    )
    submit(
        session,
        draft,
        client,
        action=SCHEDULE,
        revision=1,
        local=soon() + timedelta(days=1),
        timezone="Asia/Kolkata",
    )

    assert len(rows(session)) == 2
    assert len(zernio.puts) == 2


def test_the_command_is_written_down_before_it_is_sent(session, enabled):
    """A response lost in flight must still leave a record that the command went out."""
    draft = pushed(session)
    zernio = Zernio(status=500)

    with pytest.raises(CommandRefused):
        submit(session, draft, zernio.client(), action=PUBLISH_NOW, revision=1)

    [row] = rows(session)
    assert row.state == FAILED
    assert row.attempts == 1
    assert "cannot post today" in (row.last_error or "")


def test_a_refusal_is_not_retried(session, enabled):
    """A validation refusal is not a timeout. Retrying one makes two rejected commands."""
    draft = pushed(session)
    zernio = Zernio(status=422)
    client = zernio.client()

    with pytest.raises(CommandRefused):
        submit(session, draft, client, action=PUBLISH_NOW, revision=1)
    # The same command again finds the recorded row and sends nothing more.
    submit(session, draft, client, action=PUBLISH_NOW, revision=1)

    assert len(zernio.puts) == 1


def test_the_request_id_is_the_idempotency_key(session, enabled):
    """Zernio's own five-minute duplicate window keys on this. Ours must not be per-call."""
    zernio = Zernio()
    publication = submit(
        session, pushed(session), zernio.client(), action=PUBLISH_NOW, revision=1
    )

    assert zernio.puts[0]["request_id"] == publication.idempotency_key


def test_the_key_is_derived_and_stable():
    when = datetime(2026, 8, 12, 3, 30, tzinfo=UTC)
    assert idempotency_key(7, 2, SCHEDULE, when) == idempotency_key(7, 2, SCHEDULE, when)
    assert idempotency_key(7, 2, SCHEDULE, when) != idempotency_key(7, 3, SCHEDULE, when)
    assert idempotency_key(7, 2, SCHEDULE, when) != idempotency_key(7, 2, PUBLISH_NOW, when)


# --- the seven-day media expiry, which only bites once Pixii is the publisher ------------


def test_every_command_re_uploads_the_visual(session, enabled):
    """Measured in `push_draft`: presign returns a `/temp/` URL that expires after seven
    days, and Zernio copies the file to permanent storage only when a post *publishes*.

    That was harmless while publishing was an unbounded human act this app could not
    influence. Pixii scheduling a post two weeks out would publish text with a dead image.
    """
    draft = pushed(session)
    draft.zernio_media_url = "https://media.test/temp/stale.png"
    session.add(draft)
    session.flush()
    zernio = Zernio()

    submit(
        session,
        draft,
        zernio.client(),
        action=SCHEDULE,
        revision=1,
        local=soon(),
        timezone="Asia/Kolkata",
    )

    assert zernio.uploads == 1
    sent = zernio.puts[0]["body"]["mediaItems"][0]["url"]
    assert sent != "https://media.test/temp/stale.png"
    assert draft.zernio_media_url == sent


# --- the Inbox stops calling a clock a person -------------------------------------------


def test_a_scheduled_draft_leaves_the_human_queue(session, enabled):
    draft = pushed(session)
    assert scheduled_draft_ids(session) == set()

    submit(
        session,
        draft,
        Zernio().client(),
        action=SCHEDULE,
        revision=1,
        local=soon(),
        timezone="Asia/Kolkata",
    )

    assert scheduled_draft_ids(session) == {draft.id}


def test_cancelling_puts_it_back_in_the_human_queue(session, enabled):
    draft = pushed(session)
    client = Zernio().client()
    submit(
        session,
        draft,
        client,
        action=SCHEDULE,
        revision=1,
        local=soon(),
        timezone="Asia/Kolkata",
    )
    submit(session, draft, client, action=CANCEL_SCHEDULE, revision=1)

    assert scheduled_draft_ids(session) == set()


def test_a_refused_schedule_never_leaves_the_queue(session, enabled):
    """Only an accepted command changes where a draft sits. A failed one changes nothing."""
    draft = pushed(session)

    with pytest.raises(CommandRefused):
        submit(
            session,
            draft,
            Zernio(status=500).client(),
            action=SCHEDULE,
            revision=1,
            local=soon(),
            timezone="Asia/Kolkata",
        )

    assert scheduled_draft_ids(session) == set()


def test_an_accepted_command_records_when(session, enabled):
    publication = submit(
        session, pushed(session), Zernio().client(), action=PUBLISH_NOW, revision=1
    )

    assert publication.state == ACCEPTED
    assert publication.accepted_at is not None
    assert publication.last_error is None


# --- what `revision` counts, and what it must not --------------------------------------


def test_regenerating_the_words_invalidates_a_confirmed_command(api, session):
    draft = pushed(session)
    session.commit()

    api.post(f"/drafts/{draft.id}/regenerate-text")

    session.refresh(draft)
    assert draft.revision == 2


def test_pushing_does_not_count_as_an_edit(session):
    """`push_draft` writes `zernio_media_url` and `pushed_at`. If those bumped the revision,
    every push would invalidate the command a reviewer was about to confirm."""
    draft = a_draft(session)
    draft.visual_image = b"IMG"
    session.flush()
    before = draft.revision

    push_draft(session, draft, Zernio().client(), account_id="acct-1")

    assert draft.revision == before


def test_a_failed_redraw_does_not_count_as_an_edit(api, session, monkeypatch):
    """The last working image is still in place, so what a human would publish is unchanged.

    Bumping here would refuse a confirmed command over a picture that never changed.
    """
    draft = pushed(session)
    session.commit()
    before = draft.revision

    monkeypatch.setattr(
        "app.api_drafts.regenerate_visual",
        lambda session, draft, renderer: setattr(draft, "visual_image", None),
    )
    api.post(f"/drafts/{draft.id}/regenerate-visual")

    session.refresh(draft)
    assert draft.revision == before
    assert draft.visual_image == b"IMG"


# --- the HTTP contract: every refusal says something different --------------------------


def test_the_kill_switch_answers_403_not_503(api, session, monkeypatch):
    """Nothing is broken. The capability is off, and turning it on is a decision."""
    monkeypatch.setattr(settings, "publishing_enabled", False)
    draft = pushed(session)
    session.commit()

    response = api.post(f"/drafts/{draft.id}/publish", json={"revision": draft.revision})

    assert response.status_code == 403


def test_a_stale_revision_answers_409_with_the_current_one(api, session, enabled):
    draft = pushed(session)
    draft.edited()
    session.commit()

    response = api.post(f"/drafts/{draft.id}/publish", json={"revision": 1})

    assert response.status_code == 409
    assert response.json()["detail"]["current_revision"] == 2


def test_a_past_schedule_answers_422(api, session, enabled):
    draft = pushed(session)
    session.commit()

    response = api.post(
        f"/drafts/{draft.id}/schedule",
        json={
            "revision": draft.revision,
            "local_time": (datetime.now(UTC) - timedelta(days=1))
            .replace(tzinfo=None)
            .isoformat(),
            "timezone": "Asia/Kolkata",
        },
    )

    assert response.status_code == 422


def test_a_refusal_from_zernio_answers_502_with_its_reason(api, session, enabled, refusing):
    draft = pushed(session)
    session.commit()

    response = api.post(f"/drafts/{draft.id}/publish", json={"revision": draft.revision})

    assert response.status_code == 502
    assert "cannot post today" in response.json()["detail"]


def test_a_schedule_succeeds_and_is_listed_against_the_draft(api, session, enabled):
    draft = pushed(session)
    session.commit()

    created = api.post(
        f"/drafts/{draft.id}/schedule",
        json={
            "revision": draft.revision,
            "local_time": soon().isoformat(),
            "timezone": "Asia/Kolkata",
        },
    )
    assert created.status_code == 200
    assert created.json()["state"] == ACCEPTED

    listed = api.get(f"/drafts/{draft.id}/publications").json()
    assert [row["action"] for row in listed] == [SCHEDULE]
    assert listed[0]["timezone"] == "Asia/Kolkata"


def test_the_revision_reaches_the_api(api, session):
    """`DraftOut` is hand-mapped. Without this the review screen has nothing to confirm."""
    draft = pushed(session)
    draft.edited()
    session.commit()

    assert api.get(f"/drafts/{draft.id}").json()["revision"] == 2
