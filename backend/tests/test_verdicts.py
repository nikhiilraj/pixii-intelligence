"""A human's ruling on a published post.

Engagement spans 12.7x across this corpus at ~3 samples per template, so no aggregate can
rank anything yet. A verdict is the one form of learning that is honest at n=1 — which is
also why nothing here may be read as a performance measure.
"""

from fastapi.testclient import TestClient
from sqlmodel import Session

from app.db import get_session
from app.main import VERDICT_NOTE_MAX, app
from app.metrics import sync_metrics
from app.models.post import Post, Verdict
from tests.test_inbox import a_lap
from tests.test_metrics import FakeZernio, analytics_post


def client_with(session: Session) -> TestClient:
    app.dependency_overrides[get_session] = lambda: session
    return TestClient(app)


def saved(session: Session, zernio_id: str = "a1") -> Post:
    post = Post(
        zernio_id=zernio_id,
        platform="linkedin",
        content="The boring $325M acquisition:",
        status="published",
        likes=10,
        comments=1,
        engaged_actions=11,
    )
    session.add(post)
    session.flush()
    return post


def test_a_verdict_persists_with_its_timestamp(session):
    post = saved(session)

    body = client_with(session).post(
        f"/posts/{post.id}/verdict",
        json={"verdict": "worked", "note": "The teardown format landed; comments were real."},
    ).json()

    assert body["verdict"] == "worked"
    assert body["verdict_note"] == "The teardown format landed; comments were real."
    assert body["verdict_at"] is not None

    # `is`, not `==`: Verdict is a StrEnum, so a bare "worked" would satisfy `==` while
    # failing every identity check downstream. The column coerces on read so it cannot.
    session.refresh(post)
    assert post.verdict is Verdict.WORKED
    app.dependency_overrides.clear()


def test_a_verdict_is_resettable(session):
    """A human changes their mind once they have seen how a post aged."""
    post = saved(session)
    client = client_with(session)

    first = client.post(
        f"/posts/{post.id}/verdict", json={"verdict": "worked", "note": "Strong open."}
    ).json()
    second = client.post(
        f"/posts/{post.id}/verdict", json={"verdict": "mixed", "note": "Reach only, no replies."}
    ).json()

    assert first["verdict"] == "worked"
    assert second["verdict"] == "mixed"
    assert second["verdict_note"] == "Reach only, no replies."
    assert second["verdict_at"] >= first["verdict_at"]
    app.dependency_overrides.clear()


def test_a_verdict_can_be_cleared(session):
    """A ruling is retractable, not only changeable.

    `{"verdict": null}` is the documented clear (PRD.md:178). The note goes with it: a note is
    the *reason for a ruling*, so leaving one behind after the ruling is gone would orphan it —
    and `generation.verdict_lessons` reads notes, not verdicts, so an orphan note would keep
    teaching the generator from a judgement nobody holds any more.
    """
    post = saved(session)
    client = client_with(session)

    client.post(f"/posts/{post.id}/verdict", json={"verdict": "worked", "note": "Real comments."})
    # A note sent alongside the clear is discarded too, not stored against no ruling.
    body = client.post(
        f"/posts/{post.id}/verdict", json={"verdict": None, "note": "changed my mind"}
    ).json()

    assert body["verdict"] is None
    assert body["verdict_note"] == ""
    assert body["verdict_at"] is None

    session.refresh(post)
    assert post.verdict is None
    assert post.verdict_note == ""
    assert post.verdict_at is None
    app.dependency_overrides.clear()


def test_an_empty_body_does_not_clear_a_ruling(session):
    """The whole reason `verdict` stays required rather than becoming `Verdict | None = None`.

    An optional-with-default field would turn `{}` — an empty body, or a request that misspelt
    the key — into a silent wipe of a human's judgement. It is a 422 instead.
    """
    post = saved(session)
    client = client_with(session)
    client.post(f"/posts/{post.id}/verdict", json={"verdict": "worked", "note": "Real comments."})

    empty = client.post(f"/posts/{post.id}/verdict", json={})
    mistyped = client.post(f"/posts/{post.id}/verdict", json={"verdit": None, "note": ""})

    assert empty.status_code == 422
    assert mistyped.status_code == 422

    session.refresh(post)
    assert post.verdict is Verdict.WORKED
    assert post.verdict_note == "Real comments."
    app.dependency_overrides.clear()


def test_clearing_a_verdict_returns_the_post_to_the_inbox(session):
    """Queue 4 is `went_live_at is not null and verdict is null`, so a cleared post is waiting
    on a human again — which is the observable point of being able to retract a ruling."""
    _, post = a_lap(session, verdict=Verdict.WORKED)
    client = client_with(session)

    before = client.get("/inbox").json()["published_awaiting_verdict"]
    cleared = client.post(f"/posts/{post.id}/verdict", json={"verdict": None, "note": ""})
    after = client.get("/inbox").json()["published_awaiting_verdict"]

    assert cleared.status_code == 200
    assert [item["id"] for item in before["items"]] == []
    assert [item["id"] for item in after["items"]] == [post.id]
    app.dependency_overrides.clear()


def test_a_note_at_the_cap_is_accepted_and_one_over_is_rejected(session):
    post = saved(session)
    client = client_with(session)

    at_cap = client.post(
        f"/posts/{post.id}/verdict", json={"verdict": "mixed", "note": "x" * VERDICT_NOTE_MAX}
    )
    assert at_cap.status_code == 200

    over = client.post(
        f"/posts/{post.id}/verdict",
        json={"verdict": "mixed", "note": "x" * (VERDICT_NOTE_MAX + 1)},
    )
    assert over.status_code == 422
    assert str(VERDICT_NOTE_MAX) in str(over.json()["detail"])
    assert str(VERDICT_NOTE_MAX + 1) in str(over.json()["detail"])

    # The rejected note did not partially land: the row still carries the accepted one.
    session.refresh(post)
    assert post.verdict_note == "x" * VERDICT_NOTE_MAX
    app.dependency_overrides.clear()


def test_an_unknown_verdict_is_rejected_rather_than_coerced(session):
    """Not silently mapped onto one of the three. A wrong ruling is worse than no ruling."""
    post = saved(session)

    response = client_with(session).post(
        f"/posts/{post.id}/verdict", json={"verdict": "great", "note": ""}
    )

    assert response.status_code == 422
    detail = str(response.json()["detail"])
    assert "worked" in detail and "didnt" in detail and "mixed" in detail

    session.refresh(post)
    assert post.verdict is None
    app.dependency_overrides.clear()


def test_a_verdict_on_an_unknown_post_is_not_found(session):
    response = client_with(session).post(
        "/posts/999999/verdict", json={"verdict": "worked", "note": ""}
    )

    assert response.status_code == 404
    app.dependency_overrides.clear()


def test_a_metrics_sync_does_not_clear_a_verdict(session):
    """Pins the property that lets verdicts live on `Post` at all.

    `corpus._apply` writes an explicit field list and its only dynamic write is `setattr`
    over `_METRICS`, so no sync can reach a verdict column. If someone ever replaces that
    with a splat or a `model_validate`, this test is what tells them they erased a human's
    judgement.
    """
    payload = analytics_post("p1", likes=10, impressions=500)
    post = saved(session, zernio_id=payload["_id"])
    client_with(session).post(
        f"/posts/{post.id}/verdict", json={"verdict": "didnt", "note": "Nobody bit."}
    )
    session.refresh(post)
    ruled_at = post.verdict_at

    sync_metrics(session, FakeZernio([analytics_post("p1", likes=99, impressions=6691)]))
    session.refresh(post)

    # The sync did land — otherwise this asserts nothing.
    assert post.impressions == 6691
    assert post.likes == 99

    assert post.verdict is Verdict.DIDNT
    assert post.verdict_note == "Nobody bit."
    assert post.verdict_at == ruled_at
    app.dependency_overrides.clear()
