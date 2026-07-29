"""Ingest of posts scraped off a LinkedIn profile page.

The scrape is the only source for two things Zernio cannot give us: posts older than
Zernio's history, and repost counts (Zernio reports `shares: 0` on every LinkedIn row).
"""

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.corpus import ingest_posts, upsert_linkedin_posts
from app.db import get_session
from app.main import app
from app.models.post import Post, PostSource

CONTENT = "I graded 27 beauty SKUs and every one was leaving money on the table."


def scraped(**overrides) -> dict:
    item = {
        "urn": "urn:li:activity:7487870509611778048",
        "url": "https://www.linkedin.com/feed/update/urn:li:activity:7487870509611778048/",
        "published_at": "2026-07-28T14:04:07.398Z",
        "content": CONTENT,
        "likes": 12,
        "comments": 3,
        "shares": 2,
        "media_urls": [],
        "media_type": None,
    }
    return {**item, **overrides}


def zernio_row(session: Session, **analytics) -> Post:
    """An existing corpus row for the same post, as the analytics window delivered it."""
    ingest_posts(
        session,
        [
            {
                "_id": "z1",
                "latePostId": "l1",
                "platform": "linkedin",
                "content": CONTENT,
                "platforms": [{"platformPostId": "urn:li:share:999"}],
                "analytics": {"impressions": 6691, "engagementRate": 1.4, **analytics},
            }
        ],
    )
    return session.exec(select(Post)).one()


def client_with(session: Session) -> TestClient:
    app.dependency_overrides[get_session] = lambda: session
    return TestClient(app)


def test_a_post_the_corpus_has_never_seen_is_created(session):
    result = upsert_linkedin_posts(session, [scraped()], account="Monte Desai")

    post = session.exec(select(Post)).one()
    assert (result.created, result.updated) == (1, 0)
    assert post.source == PostSource.MANUAL
    assert post.is_external is True
    assert post.platform == "linkedin"
    assert post.account_username == "Monte Desai"
    assert post.platform_post_url.endswith("7487870509611778048/")
    assert post.published_at.year == 2026
    assert (post.likes, post.comments, post.shares) == (12, 3, 2)
    assert post.engaged_actions == 17


def test_a_scraped_post_matches_an_existing_row_on_normalised_content(session):
    existing = zernio_row(session)

    result = upsert_linkedin_posts(session, [scraped()])

    assert (result.created, result.updated) == (0, 1)
    assert session.exec(select(Post)).one().id == existing.id


def test_the_activity_urn_alone_would_match_nothing(session):
    """`urn:li:activity:…` (DOM) and `urn:li:share:…` (Zernio) name the same post
    differently. This is why the join runs on content."""
    existing = zernio_row(session)

    assert existing.platform_post_id == "urn:li:share:999"
    assert existing.platform_post_id != scraped()["urn"]


def test_the_match_survives_punctuation_and_whitespace_drift(session):
    """A post edited on LinkedIn renders slightly differently than Zernio submitted it."""
    existing = zernio_row(session)

    upsert_linkedin_posts(session, [scraped(content=CONTENT.replace(" ", "  ") + " 🙂")])

    assert session.exec(select(Post)).one().id == existing.id


def test_a_merge_never_touches_impressions(session):
    """The scrape sees someone else's profile, where impressions are author-only. A
    merge that wrote them would zero the only reach figure the corpus has."""
    zernio_row(session)

    upsert_linkedin_posts(session, [scraped()])

    assert session.exec(select(Post)).one().impressions == 6691


def test_a_merge_leaves_the_row_a_zernio_row(session):
    zernio_row(session)

    upsert_linkedin_posts(session, [scraped()])

    post = session.exec(select(Post)).one()
    assert post.source == PostSource.ZERNIO
    assert post.zernio_id == "z1"
    assert post.engagement_rate == 1.4


def test_a_merge_writes_the_repost_count_zernio_never_reports(session):
    zernio_row(session, likes=12, comments=3)

    upsert_linkedin_posts(session, [scraped()])

    post = session.exec(select(Post)).one()
    assert post.shares == 2
    assert post.engaged_actions == 17


def test_a_merge_keeps_the_higher_of_the_two_reaction_counts(session):
    """The scrape is live; Zernio's analytics can lag. Neither is authoritative, so
    whichever saw more reactions wins."""
    zernio_row(session, likes=40, comments=1)

    upsert_linkedin_posts(session, [scraped(likes=12, comments=3)])

    post = session.exec(select(Post)).one()
    assert (post.likes, post.comments) == (40, 3)
    assert post.engaged_actions == 45


def test_saves_still_count_towards_engaged_actions_after_a_merge(session):
    zernio_row(session, saves=5)

    upsert_linkedin_posts(session, [scraped()])

    assert session.exec(select(Post)).one().engaged_actions == 22


def test_re_running_the_same_scrape_creates_nothing(session):
    upsert_linkedin_posts(session, [scraped()])

    result = upsert_linkedin_posts(session, [scraped()])

    assert (result.created, result.updated) == (0, 1)
    assert len(session.exec(select(Post)).all()) == 1


def test_a_post_edited_since_the_last_scrape_is_matched_on_its_urn(session):
    """Content matching cannot cover an edit of our own earlier scrape — the urn can."""
    upsert_linkedin_posts(session, [scraped()])

    result = upsert_linkedin_posts(session, [scraped(content="Rewritten entirely.")])

    assert (result.created, result.updated) == (0, 1)
    assert len(session.exec(select(Post)).all()) == 1


def test_two_scraped_posts_are_two_rows(session):
    upsert_linkedin_posts(
        session,
        [scraped(), scraped(urn="urn:li:activity:2", content="A different post.")],
    )

    assert len(session.exec(select(Post)).all()) == 2


def test_an_image_post_keeps_only_its_first_image(session):
    upsert_linkedin_posts(
        session,
        [
            scraped(
                media_type="image",
                media_urls=["https://media.licdn.com/a", "https://media.licdn.com/b"],
            )
        ],
    )

    post = session.exec(select(Post)).one()
    assert [item["url"] for item in post.media_items] == ["https://media.licdn.com/a"]


def test_a_video_post_records_its_type_and_downloads_nothing(session):
    """Video is recorded, never fetched — the post URL is where it stays."""
    upsert_linkedin_posts(
        session,
        [scraped(media_type="video", media_urls=["https://media.licdn.com/v"])],
        with_media=True,
    )

    post = session.exec(select(Post)).one()
    assert post.media_type == "video"
    assert post.media_items == []
    assert post.local_media_path is None


def test_the_next_zernio_sync_wipes_the_shares_a_merge_wrote(session):
    """**A known ceiling, pinned so nobody assumes otherwise.**

    `sync_metrics` re-applies the analytics payload over every row it covers, and Zernio
    reports `shares: 0` on every LinkedIn row — so enrichment of a post still inside the
    50-row analytics window is undone by the next scheduled sync. Rows the scrape created
    are safe (no sync knows them), and so is any post older than the window.

    The fix belongs wherever analytics metrics are re-applied — `_apply` would have to
    stop trusting a zero it never observes — not here.
    """
    zernio_row(session)
    upsert_linkedin_posts(session, [scraped()])

    ingest_posts(
        session,
        [{"_id": "z1", "platform": "linkedin", "content": CONTENT, "analytics": {"likes": 12}}],
    )

    assert session.exec(select(Post)).one().shares == 0


def test_the_endpoint_ingests_a_scrape_payload(session):
    body = client_with(session).post(
        "/corpus/linkedin",
        json={"account": "Monte Desai", "profile": "https://x", "posts": [scraped()]},
    ).json()

    assert body == {"created": 1, "updated": 0}
    assert session.exec(select(Post)).one().account_username == "Monte Desai"
    app.dependency_overrides.clear()
