from sqlmodel import func, select

from app.corpus import ingest_posts
from app.models.post import Post


def zernio_post(zid: str, **overrides) -> dict:
    payload = {
        "_id": zid,
        "latePostId": f"late-{zid}",
        "platform": "linkedin",
        "content": "The boring $325M acquisition:",
        "publishedAt": "2026-07-28T14:04:10.211Z",
        "status": "published",
        "platformPostUrl": "https://www.linkedin.com/feed/update/urn:li:share:123/",
        "thumbnailUrl": "https://media.zernio.com/media/thumb.png",
        "mediaType": "image",
        "mediaItems": [{"type": "image", "url": "https://media.zernio.com/media/a.png"}],
        "isExternal": True,
        "analytics": {
            "impressions": 6691,
            "reach": 4200,
            "likes": 10,
            "comments": 1,
            "shares": 0,
            "saves": 0,
            "clicks": 3,
            "views": 0,
            "follows": 0,
            "engagementRate": 0.27,
        },
        "platforms": [
            {
                "platform": "linkedin",
                "platformPostId": "urn:li:share:123",
                "accountUsername": "Monte Desai",
            }
        ],
    }
    payload.update(overrides)
    return payload


def stored(session) -> list[Post]:
    return list(session.exec(select(Post)).all())


def test_persists_content_metrics_and_publication_details(session):
    ingest_posts(session, [zernio_post("a1")])

    post = stored(session)[0]
    assert post.zernio_id == "a1"
    assert post.late_post_id == "late-a1"
    assert post.platform == "linkedin"
    assert post.content == "The boring $325M acquisition:"
    assert post.published_at is not None
    assert post.platform_post_id == "urn:li:share:123"
    assert post.account_username == "Monte Desai"
    assert post.media_type == "image"
    assert post.media_items == [{"type": "image", "url": "https://media.zernio.com/media/a.png"}]
    assert post.is_external is True
    assert post.impressions == 6691
    assert post.likes == 10
    assert post.engagement_rate == 0.27


def test_engaged_actions_sums_likes_comments_shares_and_saves(session):
    ingest_posts(
        session,
        [
            zernio_post(
                "a1",
                analytics={"likes": 10, "comments": 4, "shares": 2, "saves": 1, "impressions": 99},
            )
        ],
    )

    assert stored(session)[0].engaged_actions == 17


def test_reingesting_updates_rather_than_duplicating(session):
    ingest_posts(session, [zernio_post("a1")])
    ingest_posts(session, [zernio_post("a1", analytics={"impressions": 7000, "likes": 42})])

    posts = stored(session)
    assert len(posts) == 1
    assert posts[0].impressions == 7000
    assert posts[0].likes == 42


def test_ingests_many_posts_in_one_pass(session):
    ingest_posts(session, [zernio_post(f"a{i}") for i in range(12)])

    assert session.exec(select(func.count()).select_from(Post)).one() == 12


def test_a_post_missing_its_analytics_block_stores_zeroed_metrics(session):
    ingest_posts(session, [zernio_post("a1", analytics={})])

    post = stored(session)[0]
    assert post.impressions == 0
    assert post.engaged_actions == 0


def test_a_post_without_platform_details_still_stores(session):
    ingest_posts(session, [zernio_post("a1", platforms=[])])

    post = stored(session)[0]
    assert post.platform_post_id is None
    assert post.account_username is None


def test_returns_how_many_posts_were_created_and_updated(session):
    first = ingest_posts(session, [zernio_post("a1"), zernio_post("a2")])
    second = ingest_posts(session, [zernio_post("a2"), zernio_post("a3")])

    assert (first.created, first.updated) == (2, 0)
    assert (second.created, second.updated) == (1, 1)
