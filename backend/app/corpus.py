from dataclasses import dataclass
from datetime import UTC, datetime

from sqlmodel import Session, select

from app.models.post import Post

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


def ingest_posts(session: Session, payloads: list[dict]) -> IngestResult:
    """Upsert Zernio's posts into the corpus, keyed on Zernio's own post id.

    Re-running is safe and expected: metrics change as posts accumulate engagement, so
    ingest updates in place rather than appending a second row.
    """
    result = IngestResult()
    for payload in payloads:
        zernio_id = payload.get("_id")
        if not zernio_id:
            continue

        post = session.exec(select(Post).where(Post.zernio_id == zernio_id)).first()
        if post is None:
            post = Post(zernio_id=zernio_id)
            result.created += 1
        else:
            result.updated += 1

        _apply(post, payload)
        session.add(post)

    session.flush()
    return result
