from fastapi.testclient import TestClient
from sqlmodel import Session

from app.db import get_session
from app.main import app
from app.models.post import Post


def client_with(session: Session) -> TestClient:
    app.dependency_overrides[get_session] = lambda: session
    return TestClient(app)


def saved(session: Session) -> Post:
    post = Post(
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
    app.dependency_overrides.clear()
