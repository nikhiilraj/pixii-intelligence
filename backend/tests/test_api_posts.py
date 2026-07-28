from fastapi.testclient import TestClient
from sqlmodel import Session

from app.db import get_session
from app.main import app
from app.models.post import Post


def client_with(session: Session) -> TestClient:
    app.dependency_overrides[get_session] = lambda: session
    return TestClient(app)


def make_post(zernio_id: str, engaged: int) -> Post:
    return Post(
        zernio_id=zernio_id,
        platform="linkedin",
        content=f"post {zernio_id}",
        impressions=100,
        likes=engaged,
        engaged_actions=engaged,
    )


def test_lists_posts_ranked_by_engaged_actions(session):
    session.add_all([make_post("low", 3), make_post("high", 185), make_post("mid", 48)])
    session.flush()

    body = client_with(session).get("/posts").json()

    assert [p["zernio_id"] for p in body] == ["high", "mid", "low"]
    app.dependency_overrides.clear()


def test_lists_posts_with_their_metrics(session):
    session.add(make_post("a1", 42))
    session.flush()

    post = client_with(session).get("/posts").json()[0]

    assert post["impressions"] == 100
    assert post["engaged_actions"] == 42
    assert post["content"] == "post a1"
    app.dependency_overrides.clear()


def test_an_empty_corpus_returns_an_empty_list(session):
    body = client_with(session).get("/posts").json()

    assert body == []
    app.dependency_overrides.clear()
