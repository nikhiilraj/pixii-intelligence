from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlmodel import Session

from app.db import get_session
from app.main import app
from app.models.draft import Draft
from app.models.metric import MetricSnapshot
from app.models.post import Post
from app.models.template import TemplateKind
from app.templates import approve, create_template


def client_with(session: Session) -> TestClient:
    app.dependency_overrides[get_session] = lambda: session
    return TestClient(app)


def add_post(session, zid, *, platform="linkedin", engaged=10, impressions=100, days_ago=0):
    post = Post(
        zernio_id=zid,
        late_post_id=f"late-{zid}",
        platform=platform,
        content=f"post {zid}",
        engaged_actions=engaged,
        impressions=impressions,
        likes=engaged,
        published_at=datetime.now(UTC) - timedelta(days=days_ago),
    )
    session.add(post)
    session.flush()
    return post


def names(body) -> list[str]:
    return [p["zernio_id"] for p in body]


def test_every_post_appears_in_one_view(session):
    add_post(session, "a")
    add_post(session, "b", platform="twitter")

    body = client_with(session).get("/posts").json()

    assert sorted(names(body)) == ["a", "b"]
    app.dependency_overrides.clear()


def test_filtering_by_channel(session):
    add_post(session, "li")
    add_post(session, "tw", platform="twitter")

    body = client_with(session).get("/posts?platform=twitter").json()

    assert names(body) == ["tw"]
    app.dependency_overrides.clear()


def test_filtering_by_date_range(session):
    add_post(session, "old", days_ago=40)
    add_post(session, "recent", days_ago=2)
    since = (datetime.now(UTC) - timedelta(days=7)).date().isoformat()

    body = client_with(session).get(f"/posts?since={since}").json()

    assert names(body) == ["recent"]
    app.dependency_overrides.clear()


def test_filtering_by_an_upper_date_bound(session):
    add_post(session, "old", days_ago=40)
    add_post(session, "recent", days_ago=2)
    until = (datetime.now(UTC) - timedelta(days=7)).date().isoformat()

    body = client_with(session).get(f"/posts?until={until}").json()

    assert names(body) == ["old"]
    app.dependency_overrides.clear()


def test_posts_rank_by_engaged_actions_by_default(session):
    add_post(session, "low", engaged=3, impressions=9000)
    add_post(session, "high", engaged=185, impressions=100)

    body = client_with(session).get("/posts").json()

    assert names(body) == ["high", "low"]
    app.dependency_overrides.clear()


def test_sorting_by_any_metric(session):
    add_post(session, "low", engaged=185, impressions=100)
    add_post(session, "high", engaged=3, impressions=9000)

    body = client_with(session).get("/posts?sort=impressions").json()

    assert names(body) == ["high", "low"]
    app.dependency_overrides.clear()


def test_sorting_ascending_when_asked(session):
    add_post(session, "low", engaged=3)
    add_post(session, "high", engaged=185)

    body = client_with(session).get("/posts?sort=engaged_actions&order=asc").json()

    assert names(body) == ["low", "high"]
    app.dependency_overrides.clear()


def test_an_unknown_sort_field_is_rejected_rather_than_ignored(session):
    """Silently falling back would show a different ranking than the one asked for."""
    add_post(session, "a")

    response = client_with(session).get("/posts?sort=; DROP TABLE post")

    assert response.status_code == 422
    app.dependency_overrides.clear()


def test_filtering_by_the_template_that_produced_the_post(session):
    hook = create_template(session, kind=TemplateKind.HOOK, name="h", body={})
    structure = create_template(session, kind=TemplateKind.STRUCTURE, name="s", body={})
    approve(session, hook)
    approve(session, structure)
    mine = add_post(session, "mine")
    add_post(session, "organic")
    session.add(
        Draft(
            idea="i",
            hook_family=hook.family_id,
            hook_version=1,
            structure_family=structure.family_id,
            structure_version=1,
            zernio_post_id=mine.late_post_id,
        )
    )
    session.flush()

    body = client_with(session).get(f"/posts?template_family={hook.family_id}").json()

    assert names(body) == ["mine"]
    app.dependency_overrides.clear()


def test_performance_over_time_is_available_for_charting(session):
    post = add_post(session, "a")
    for day, likes in ((3, 5), (2, 20), (1, 44)):
        session.add(
            MetricSnapshot(
                post_id=post.id or 0,
                captured_at=datetime.now(UTC) - timedelta(days=day),
                likes=likes,
                engaged_actions=likes,
                impressions=likes * 30,
            )
        )
    session.flush()

    body = client_with(session).get(f"/posts/{post.id}/history").json()

    assert [point["engaged_actions"] for point in body] == [5, 20, 44]
    app.dependency_overrides.clear()


def test_history_for_a_post_with_no_snapshots_is_empty_not_an_error(session):
    post = add_post(session, "a")

    response = client_with(session).get(f"/posts/{post.id}/history")

    assert response.status_code == 200
    assert response.json() == []
    app.dependency_overrides.clear()


def test_filters_combine(session):
    add_post(session, "li-recent", days_ago=1)
    add_post(session, "li-old", days_ago=40)
    add_post(session, "tw-recent", platform="twitter", days_ago=1)
    since = (datetime.now(UTC) - timedelta(days=7)).date().isoformat()

    body = client_with(session).get(f"/posts?platform=linkedin&since={since}").json()

    assert names(body) == ["li-recent"]
    app.dependency_overrides.clear()
