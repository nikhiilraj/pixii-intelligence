from fastapi.testclient import TestClient
from sqlmodel import Session

from app.db import get_session
from app.main import app
from app.models.draft import Draft
from app.models.post import Post


def client_with(session: Session) -> TestClient:
    app.dependency_overrides[get_session] = lambda: session
    return TestClient(app)


def saved(session: Session, late_post_id: str | None = None) -> Post:
    post = Post(
        late_post_id=late_post_id,
        zernio_id="a1",
        platform="linkedin",
        content="The boring $325M acquisition:\n\nMiss Mouth gets snapped up.",
        status="published",
        platform_post_url="https://www.linkedin.com/feed/update/urn:li:share:123/",
        account_username="Monte Desai",
        media_type="image",
        local_media_path="a1.png",
        impressions=6691,
        reach=4200,
        likes=10,
        comments=1,
        engagement_rate=0.27,
        engaged_actions=11,
    )
    session.add(post)
    session.flush()
    return post


def test_returns_the_full_post_with_media_metrics_and_publication_details(session):
    post = saved(session)

    body = client_with(session).get(f"/posts/{post.id}").json()

    assert body["content"].startswith("The boring $325M acquisition:")
    assert body["local_media_path"] == "a1.png"
    assert body["account_username"] == "Monte Desai"
    assert body["platform_post_url"].endswith("urn:li:share:123/")
    assert body["impressions"] == 6691
    assert body["engaged_actions"] == 11
    app.dependency_overrides.clear()


def test_an_unknown_post_is_not_found(session):
    response = client_with(session).get("/posts/999999")

    assert response.status_code == 404
    # The detail, not just the status. FastAPI answers 404 for an unrouted path too, so a
    # status-only assertion passes with the route deleted — measured: renaming this route
    # left the assertion green. The body is the only proof the handler ran.
    assert response.json()["detail"] == "no post 999999"
    app.dependency_overrides.clear()


def test_a_post_can_be_held_out_of_extraction_and_let_back_in(session):
    post = saved(session)
    client = client_with(session)

    held_out = client.post(f"/posts/{post.id}/exclude", json={"excluded": True}).json()
    assert held_out["excluded_from_extraction"] is True

    let_back = client.post(f"/posts/{post.id}/exclude", json={"excluded": False}).json()
    assert let_back["excluded_from_extraction"] is False
    app.dependency_overrides.clear()


def test_excluding_an_unknown_post_is_not_found(session):
    response = client_with(session).post("/posts/999999/exclude", json={"excluded": True})

    assert response.status_code == 404
    assert response.json()["detail"] == "no post 999999"
    app.dependency_overrides.clear()


def generated(session: Session, late_post_id: str) -> Draft:
    """A draft that was pushed and went live as the post carrying `late_post_id`."""
    draft = Draft(
        idea="what a listing image is actually for",
        hook_family="hook-a",
        structure_family="structure-a",
        hook_text="Your listing image is not decoration.",
        body_text="It is the only thing a shopper reads.",
        zernio_post_id=late_post_id,
    )
    session.add(draft)
    session.flush()
    return draft


def test_the_draft_behind_a_generated_post_is_served_by_its_own_route(session):
    """The join `draft_for_post` already does, given an HTTP surface.

    `lineage` is asserted because it is only present if the route went through `_out` —
    `DraftOut` is hand-mapped, and a second hand-rolled shape here would drift from it.
    """
    post = saved(session, late_post_id="late-1")
    draft = generated(session, "late-1")

    response = client_with(session).get(f"/posts/{post.id}/draft")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == draft.id
    assert body["zernio_post_id"] == "late-1"
    assert body["full_text"].startswith("Your listing image is not decoration.")
    assert "lineage" in body
    app.dependency_overrides.clear()


def test_a_post_with_no_draft_behind_it_answers_null_rather_than_not_found(session):
    """The common case, not an error: 57 published posts carry no lineage.

    Both halves are asserted. A body-only check would also pass against a 404, whose
    `{"detail": ...}` is not `None` but would tell a reader their post does not exist.
    """
    post = saved(session, late_post_id="late-2")

    response = client_with(session).get(f"/posts/{post.id}/draft")

    assert response.status_code == 200
    assert response.json() is None
    app.dependency_overrides.clear()


def test_an_ingested_post_carrying_no_late_post_id_also_answers_null(session):
    """`draft_for_post`'s early return — a different path from "no matching draft", and the
    shape of every post ingested from Zernio's history rather than generated here."""
    post = saved(session)

    response = client_with(session).get(f"/posts/{post.id}/draft")

    assert response.status_code == 200
    assert response.json() is None
    app.dependency_overrides.clear()


def test_asking_for_the_draft_behind_an_unknown_post_is_not_found(session):
    """The third outcome, kept distinct from the second: no such post at all.

    The detail is asserted, not just the status. An unrouted path is a 404 too, so a
    status-only check passes before the route exists and is no evidence that it answered —
    it did, in this test's first run.
    """
    response = client_with(session).get("/posts/999999/draft")

    assert response.status_code == 404
    assert response.json()["detail"] == "no post 999999"
    app.dependency_overrides.clear()
