from sqlmodel import func, select

from app.corpus import ingest_history, ingest_posts
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


# --- /v1/posts history --------------------------------------------------------------
# A different payload shape from /analytics: `_id` is what analytics calls `latePostId`,
# there is no `analytics` block at all, and platform, publication time and URL all live
# on the per-platform entry rather than at the top level.


def entry(platform: str = "linkedin", **overrides) -> dict:
    payload = {
        "platform": platform,
        "status": "published",
        "platformPostId": f"urn:li:share:{platform}",
        "platformPostUrl": f"https://example.test/{platform}",
        "publishedAt": "2026-02-17T09:00:00.000Z",
        "accountId": {"username": "Monte Desai"},
    }
    payload.update(overrides)
    return payload


def history_post(pid: str, *, status: str = "published", platforms=None, **overrides) -> dict:
    payload = {
        "_id": pid,
        "content": "Fruitables spends $20,000+ to design a listing.",
        "status": status,
        "scheduledFor": "2026-02-17T08:55:00.000Z",
        "mediaItems": [{"type": "image", "url": "https://media.zernio.com/media/a.png"}],
        "metadata": {},
        "platforms": [entry()] if platforms is None else platforms,
    }
    payload.update(overrides)
    return payload


def test_recovers_a_published_linkedin_post_the_analytics_window_never_carried(session):
    result = ingest_history(session, [history_post("h1")])

    post = stored(session)[0]
    assert result.created == 1
    # The join key: /v1/posts._id is what the analytics payload calls latePostId.
    assert post.late_post_id == "h1"
    assert post.platform == "linkedin"
    assert post.content == "Fruitables spends $20,000+ to design a listing."
    assert post.published_at is not None
    assert post.platform_post_id == "urn:li:share:linkedin"
    assert post.platform_post_url == "https://example.test/linkedin"
    assert post.account_username == "Monte Desai"
    assert post.media_type == "image"


def test_a_post_with_no_analytics_block_at_all_ingests_with_zero_metrics(session):
    """These rows carry text and no metrics. Text is what extraction reads."""
    ingest_history(session, [history_post("h1")])

    post = stored(session)[0]
    assert (post.impressions, post.engaged_actions, post.engagement_rate) == (0, 0, 0.0)


def test_a_post_already_in_the_corpus_from_analytics_is_left_alone(session):
    """Analytics and /v1/posts identify the same post differently — insert-only, or the
    27 posts that already have metrics would be duplicated with zeroed twins."""
    ingest_posts(session, [zernio_post("a1", latePostId="h1")])

    result = ingest_history(session, [history_post("h1"), history_post("h2")])

    posts = stored(session)
    assert (result.created, result.skipped) == (1, 1)
    assert len(posts) == 2
    assert {p.late_post_id for p in posts} == {"h1", "h2"}
    assert next(p for p in posts if p.late_post_id == "h1").impressions == 6691


def test_running_it_twice_creates_nothing_the_second_time(session):
    ingest_history(session, [history_post("h1")])
    second = ingest_history(session, [history_post("h1")])

    assert (second.created, second.skipped) == (0, 1)
    assert len(stored(session)) == 1


def test_drafts_are_not_corpus_material(session):
    """103 of the 155 posts on the account are drafts. A draft is not evidence."""
    result = ingest_history(session, [history_post("h1", status="draft")])

    assert (result.created, stored(session)) == (0, [])


def test_other_channels_are_left_to_their_own_slice(session):
    """Youtube analytics rows carry no latePostId, so they cannot be deduped against
    /v1/posts — backfilling them would duplicate every video. LinkedIn only."""
    result = ingest_history(
        session,
        [history_post("h1", platforms=[entry("twitter")]), history_post("h2")],
    )

    assert result.created == 1
    assert [p.platform for p in stored(session)] == ["linkedin"]


def test_a_crosspost_takes_its_linkedin_identity_not_the_first_entry(session):
    ingest_history(
        session, [history_post("h1", platforms=[entry("twitter"), entry("linkedin")])]
    )

    post = stored(session)[0]
    assert post.platform == "linkedin"
    assert post.platform_post_id == "urn:li:share:linkedin"
