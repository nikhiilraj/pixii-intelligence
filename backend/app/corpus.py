import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlmodel import Session, select

from app.media import download_post_media
from app.models.post import Post, PostSource

_METRICS = (
    "impressions",
    "reach",
    "likes",
    "comments",
    "shares",
    "saves",
    "clicks",
    "views",
    "follows",
)

# The primary success measure. Engagement rate and impressions are kept, but a count of
# real reactions is what ranks a post — see the spec for why the alternatives disagree.
_ENGAGED = ("likes", "comments", "shares", "saves")


@dataclass
class IngestResult:
    created: int = 0
    updated: int = 0
    # Posts deliberately left as they are — the history backfill never overwrites a row
    # the analytics window already filled in.
    skipped: int = 0

    @property
    def total(self) -> int:
        return self.created + self.updated


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _apply(post: Post, payload: dict) -> None:
    analytics = payload.get("analytics") or {}
    # The per-platform entry carries the platform's own post id (a LinkedIn URN) and the
    # account it went out from. A post always has exactly one platform in practice, but
    # an empty list is tolerated rather than assumed away.
    platform_entry = (payload.get("platforms") or [{}])[0]

    post.late_post_id = payload.get("latePostId")
    post.platform = payload.get("platform") or ""
    post.content = payload.get("content") or ""
    post.status = payload.get("status") or ""
    post.published_at = _parse_time(payload.get("publishedAt"))
    post.scheduled_for = _parse_time(payload.get("scheduledFor"))
    post.platform_post_id = platform_entry.get("platformPostId")
    post.platform_post_url = payload.get("platformPostUrl")
    post.account_username = platform_entry.get("accountUsername")
    post.media_type = payload.get("mediaType")
    post.thumbnail_url = payload.get("thumbnailUrl")
    post.media_items = payload.get("mediaItems") or []
    post.is_external = bool(payload.get("isExternal"))

    for metric in _METRICS:
        setattr(post, metric, int(analytics.get(metric) or 0))
    post.engagement_rate = float(analytics.get("engagementRate") or 0.0)
    post.engaged_actions = sum(int(analytics.get(name) or 0) for name in _ENGAGED)
    post.metrics_updated_at = datetime.now(UTC)


def ingest_posts(
    session: Session, payloads: list[dict], *, with_media: bool = False
) -> IngestResult:
    """Upsert Zernio's posts into the corpus, keyed on Zernio's own post id.

    Re-running is safe and expected: metrics change as posts accumulate engagement, so
    ingest updates in place rather than appending a second row.

    Media download is opt-in so that tests and metric-only refreshes stay offline.
    """
    result = IngestResult()
    for payload in payloads:
        zernio_id = payload.get("_id")
        if not zernio_id:
            continue

        # Scoped to synced rows: a manual row must never be matched, overwritten or
        # removed by a sync, because nothing upstream knows it exists.
        post = session.exec(
            select(Post).where(
                Post.zernio_id == zernio_id, Post.source == PostSource.ZERNIO
            )
        ).first()
        if post is None:
            post = Post(zernio_id=zernio_id)
            result.created += 1
        else:
            result.updated += 1

        _apply(post, payload)
        if with_media:
            # Never lets a media failure cost us the post — it returns None instead.
            post.local_media_path = download_post_media(post) or post.local_media_path
        session.add(post)

    session.flush()
    return result


# The only channel this backfill covers. See `ingest_history` for why.
_LINKEDIN = "linkedin"


def _history_payload(payload: dict, entry: dict) -> dict:
    """Reshape a `/v1/posts` row into the shape `/analytics` returns, so one upsert serves
    both endpoints.

    The two describe the same post differently: `_id` here is what analytics calls
    `latePostId`, there is no `analytics` block at all, and the platform, publication time
    and post URL live on the per-platform entry rather than at the top level. `isExternal`
    and `thumbnailUrl` have no equivalent here and are left unset.
    """
    account = entry.get("accountId")
    media_items = payload.get("mediaItems") or []
    return {
        # The platform entry's own id: unique per (post, channel), so a crosspost keeps
        # one row per channel exactly as the analytics window would have delivered it.
        "_id": entry.get("_id") or f"{payload['_id']}:{entry.get('platform')}",
        "latePostId": payload.get("_id"),
        "content": payload.get("content"),
        "status": payload.get("status"),
        "publishedAt": entry.get("publishedAt"),
        "scheduledFor": payload.get("scheduledFor"),
        "platform": entry.get("platform"),
        "platformPostUrl": entry.get("platformPostUrl"),
        "mediaItems": media_items,
        "mediaType": media_items[0].get("type") if media_items else None,
        "platforms": [
            {
                **entry,
                "accountUsername": account.get("username")
                if isinstance(account, dict)
                else None,
            }
        ],
    }


def ingest_history(
    session: Session, payloads: list[dict], *, with_media: bool = False
) -> IngestResult:
    """Recover published LinkedIn posts that the analytics window never carried.

    `/analytics` is a recent 50-row window, not the account: 34 of Monte's LinkedIn posts
    are published but only 27 reached the corpus. The missing ones carry no metrics — but
    they carry text, and text is what template extraction reads.

    **Insert-only.** These payloads have no metrics at all, so applying one over a row the
    analytics endpoint already filled in would zero it. A post already in the corpus is
    matched on `(latePostId, platform)` and left untouched.

    ponytail: LinkedIn only, and published only. Youtube's analytics rows carry no
    `latePostId` at all, so they cannot be matched against `/v1/posts` and backfilling
    them would duplicate every video; drafts are not evidence about anything. Widen this
    when the other channels get their own slice.
    """
    known = {
        (post.late_post_id, post.platform)
        for post in session.exec(select(Post).where(Post.source == PostSource.ZERNIO)).all()
    }

    skipped = 0
    fresh: list[dict] = []
    for payload in payloads:
        post_id = payload.get("_id")
        if not post_id or payload.get("status") != "published":
            continue
        for entry in payload.get("platforms") or []:
            if entry.get("platform") != _LINKEDIN:
                continue
            if (post_id, _LINKEDIN) in known:
                skipped += 1
                continue
            fresh.append(_history_payload(payload, entry))

    result = ingest_posts(session, fresh, with_media=with_media)
    result.skipped = skipped
    return result


class ManualPostRejected(ValueError):
    """A manual corpus item that carries no text is no evidence about anything."""


def add_manual_post(
    session: Session,
    *,
    content: str,
    author: str | None = None,
    platform: str = "linkedin",
    engaged_actions: int = 0,
    impressions: int = 0,
    published_at: datetime | None = None,
    note: str = "",
) -> Post:
    """Add a post Zernio does not carry — a creator post, a screenshot's text, a paste.

    Extraction treats these as evidence like any other post, which is the point: the
    template library should be able to learn from material the API cannot reach. They are
    marked by source so Pixii's own history stays distinguishable from reference material.
    """
    if not content.strip():
        raise ManualPostRejected("a manual corpus item needs text")

    post = Post(
        # Namespaced so it can never collide with a Zernio id.
        zernio_id=f"manual:{uuid.uuid4().hex}",
        source=PostSource.MANUAL,
        platform=platform,
        content=content.strip(),
        account_username=author,
        engaged_actions=engaged_actions,
        impressions=impressions,
        published_at=published_at,
        status="external",
        metrics_updated_at=datetime.now(UTC),
    )
    post.likes = engaged_actions
    session.add(post)
    session.flush()
    return post


def _content_key(content: str) -> str:
    """The join key between a LinkedIn scrape and the corpus.

    **Not the URN.** The profile DOM names a post `urn:li:activity:<id>` and Zernio names
    the same post `urn:li:share:<id>` — different namespaces, so joining on them matches
    zero rows. Normalised text does match, and it absorbs the whitespace, punctuation and
    emoji drift between the two renderings. 120 characters is enough to be unique across
    the account and short enough that a trailing edit does not break the match.
    """
    return re.sub(r"\W+", "", content or "").lower()[:120]


def _scraped_post(item: dict, account: str | None, scrape_id: str) -> Post:
    media_urls = item.get("media_urls") or []
    media_type = item.get("media_type")
    return Post(
        zernio_id=scrape_id,
        source=PostSource.MANUAL,
        platform=_LINKEDIN,
        content=(item.get("content") or "").strip(),
        status="external",
        is_external=True,
        account_username=account,
        platform_post_url=item.get("url"),
        published_at=_parse_time(item.get("published_at")),
        media_type=media_type,
        # ponytail: the cover image only. A carousel's later slides are not stored, so
        # visual extraction sees slide one and nothing else; video is recorded by type
        # and left on LinkedIn. Widen when a slice actually needs the rest.
        media_items=(
            [{"type": "image", "url": media_urls[0]}]
            if media_type == "image" and media_urls
            else []
        ),
    )


def upsert_linkedin_posts(
    session: Session,
    items: list[dict],
    *,
    account: str | None = None,
    with_media: bool = False,
) -> IngestResult:
    """Fold posts scraped off a LinkedIn profile page into the corpus.

    The scrape is the only source for two things Zernio cannot supply: posts older than
    its history, and **repost counts** — Zernio reports `shares: 0` on every LinkedIn row,
    and `engaged_actions` (the ranking key) counts shares, so a corpus where only some
    rows carry them ranks wrongly.

    Matching is unscoped by source, because the usual case is an existing Zernio row being
    enriched rather than a new post. A match updates reactions only: impressions are
    author-only and invisible when scraping someone's profile, so writing them would
    replace a real figure with a zero.
    """
    existing = list(session.exec(select(Post).where(Post.platform == _LINKEDIN)).all())
    by_content: dict[str, Post] = {}
    for post in existing:
        by_content.setdefault(_content_key(post.content), post)
    # Second key, for a post edited since we last scraped it: its text no longer matches
    # but its urn still does, so a re-run updates rather than duplicating.
    by_scrape_id = {post.zernio_id: post for post in existing}

    result = IngestResult()
    for item in items:
        content_key = _content_key(item.get("content") or "")
        scrape_id = f"linkedin:{item['urn']}"

        match = by_content.get(content_key) or by_scrape_id.get(scrape_id)
        if match is None:
            post = _scraped_post(item, account, scrape_id)
            result.created += 1
            by_content.setdefault(content_key, post)
            by_scrape_id[scrape_id] = post
            if with_media:
                post.local_media_path = download_post_media(post)
        else:
            post = match
            result.updated += 1

        # The scrape is live and Zernio's analytics can lag, so neither side is
        # authoritative on reactions — whichever saw more wins. Shares are the exception:
        # only the scrape ever has them.
        post.likes = max(post.likes, int(item.get("likes") or 0))
        post.comments = max(post.comments, int(item.get("comments") or 0))
        post.shares = int(item.get("shares") or 0)
        post.engaged_actions = post.likes + post.comments + post.shares + post.saves
        post.metrics_updated_at = datetime.now(UTC)
        session.add(post)

    session.flush()
    return result
