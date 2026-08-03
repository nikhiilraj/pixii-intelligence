from datetime import datetime

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.config import settings
from app.corpus import PostSource, add_manual_post, ingest_posts
from app.db import get_session
from app.extraction import propose_hooks
from app.main import app
from app.models.post import Post


def client_with(session: Session) -> TestClient:
    app.dependency_overrides[get_session] = lambda: session
    return TestClient(app)


class FakeLLM:
    def __init__(self):
        self.user = ""

    def complete_json(self, system: str, user: str, images=()) -> dict:
        self.user = user
        return {"hooks": [{"name": "n", "pattern": "{a}"}]}


CREATOR_POST = {
    "content": "A creator post Monte sent over LinkedIn.",
    "author": "Some Creator",
    "engaged_actions": 900,
    "impressions": 40000,
}


def test_a_post_that_never_came_from_zernio_can_be_added(session):
    post = add_manual_post(session, **CREATOR_POST)

    assert post.content.startswith("A creator post")
    assert post.engaged_actions == 900


def test_a_manual_post_records_no_per_metric_figure_it_was_never_given(session):
    """`engaged_actions` is one total. Splaying it onto `likes` invents a measurement.

    The row it was written on claimed 1240 likes off a 1240-reaction total, and everything
    reading `likes` — `/posts?sort=likes`, `metrics.record_snapshots` — believed it. A zero
    with a known total beside it is honest; a confident wrong number is not.
    """
    post = add_manual_post(session, **CREATOR_POST)

    assert (post.likes, post.comments, post.shares, post.saves) == (0, 0, 0, 0)
    assert post.engaged_actions == 900


def test_a_manual_post_is_marked_by_source(session):
    manual = add_manual_post(session, **CREATOR_POST)

    assert manual.source == PostSource.MANUAL


def test_a_synced_post_is_marked_as_such(session):
    ingest_posts(session, [{"_id": "z1", "platform": "linkedin", "content": "ours"}])

    assert session.exec(select(Post)).one().source == PostSource.ZERNIO


def test_a_zernio_sync_does_not_remove_or_overwrite_manual_posts(session):
    manual = add_manual_post(session, **CREATOR_POST)

    ingest_posts(session, [{"_id": "z1", "platform": "linkedin", "content": "ours"}])

    kept = session.get(Post, manual.id)
    assert kept is not None
    assert kept.content.startswith("A creator post")
    assert kept.source == PostSource.MANUAL


def test_manual_posts_get_a_distinct_id_that_cannot_collide_with_zernio(session):
    first = add_manual_post(session, **CREATOR_POST)
    second = add_manual_post(session, content="Another one.")

    assert first.zernio_id != second.zernio_id
    assert first.zernio_id.startswith("manual:")


def test_a_manual_post_in_the_voice_account_is_evidence_like_any_other(session):
    add_manual_post(
        session,
        content="A post Monte pasted in himself.",
        author=settings.voice_account,
        # A paste must carry its date to be evidence — extraction excludes undated posts.
        published_at=datetime(2026, 6, 1),
    )
    llm = FakeLLM()

    propose_hooks(session, llm)

    assert "A post Monte pasted in himself." in llm.user


def test_a_pasted_creator_post_is_reference_material_not_voice_evidence(session):
    creator = add_manual_post(session, **CREATOR_POST)
    llm = FakeLLM()

    assert propose_hooks(session, llm) == []
    assert session.get(Post, creator.id) is not None


def test_a_manual_post_with_no_content_is_rejected(session):
    response = client_with(session).post("/corpus/manual", json={"content": "   "})

    assert response.status_code == 422
    app.dependency_overrides.clear()


def test_adding_a_manual_post_through_the_api(session):
    body = client_with(session).post("/corpus/manual", json=CREATOR_POST).json()

    assert body["source"] == "manual"
    assert body["engaged_actions"] == 900
    app.dependency_overrides.clear()


def test_the_corpus_can_be_filtered_to_one_source(session):
    add_manual_post(session, **CREATOR_POST)
    ingest_posts(session, [{"_id": "z1", "platform": "linkedin", "content": "ours"}])

    manual = client_with(session).get("/posts?source=manual").json()

    assert [p["source"] for p in manual] == ["manual"]
    app.dependency_overrides.clear()
