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
