import logging
from dataclasses import dataclass
from typing import Protocol

from sqlmodel import Session, col, select

from app.corpus import ingest_posts
from app.models.draft import Draft
from app.models.metric import MetricSnapshot
from app.models.post import Post
from app.models.template import Template

log = logging.getLogger("pixii.metrics")

_SNAPSHOT_FIELDS = (
    "impressions",
    "reach",
    "likes",
    "comments",
    "shares",
    "saves",
    "clicks",
    "views",
    "engagement_rate",
    "engaged_actions",
)


class AnalyticsSource(Protocol):
    def fetch_posts(self, platform: str | None = None) -> list[dict]: ...


@dataclass
class TemplatePerformance:
    """What one version of one template has actually done.

    `sample_count` travels with every aggregate on purpose: with roughly three posts per
    template against a measured 12.7x variance in this corpus, a number without its n is
    a number that will be over-trusted.
    """

    family: str
    version: int
    kind: str
    name: str
    status: str
    sample_count: int
    total_engaged_actions: int
    total_impressions: int

    @property
    def mean_engaged_actions(self) -> float:
        return self.total_engaged_actions / self.sample_count if self.sample_count else 0.0


def record_snapshots(session: Session, posts: list[Post]) -> int:
    """Append the current numbers for each post to the history."""
    for post in posts:
        session.add(
            MetricSnapshot(
                post_id=post.id or 0,
                **{field: getattr(post, field) for field in _SNAPSHOT_FIELDS},
            )
        )
    session.flush()
    return len(posts)


def sync_metrics(session: Session, source: AnalyticsSource) -> dict:
    """Refresh every post's metrics and append a snapshot for each.

    Safe and expected to run on a schedule: posts upsert on Zernio's own id and each run
    adds one reading per post rather than replacing the previous one.
    """
    payloads = source.fetch_posts()
    result = ingest_posts(session, payloads)

    touched_ids = [p.get("_id") for p in payloads if p.get("_id")]
    posts = (
        list(session.exec(select(Post).where(col(Post.zernio_id).in_(touched_ids))).all())
        if touched_ids
        else []
    )
    snapshots = record_snapshots(session, posts)

    log.info("metrics sync: %d fetched, %d snapshots", len(payloads), snapshots)
    return {"fetched": len(payloads), "snapshots": snapshots, **result.__dict__}


def draft_for_post(session: Session, post: Post) -> Draft | None:
    """The draft that produced this post, if this app produced it.

    **The join is `Draft.zernio_post_id == Post.late_post_id`.** The create response's `_id`
    surfaces in the analytics payload as `latePostId`; `analytics._id` is a different
    identifier entirely and matching on it finds nothing. Verified 13/13 against the live
    account — see `.scratch/v1/seams.md`.
    """
    if not post.late_post_id:
        return None
    return session.exec(
        select(Draft).where(Draft.zernio_post_id == post.late_post_id)
    ).first()


def _posts_by_late_id(session: Session) -> dict[str, Post]:
    return {
        post.late_post_id: post
        for post in session.exec(select(Post)).all()
        if post.late_post_id
    }


def template_performance(session: Session) -> list[TemplatePerformance]:
    """Per-template-version performance, each aggregate carrying its sample count.

    Only posts this app generated contribute: everything ingested from Zernio's history,
    and anything Monte writes outside the app, has no lineage and so cannot be attributed.
    Those posts still appear in post-level views — they are simply not evidence about a
    template.

    Deliberately returns no ranking. See the spec: at this sample size a ranking would be
    fitting noise while looking quantitative.
    """
    posts = _posts_by_late_id(session)
    drafts = list(session.exec(select(Draft)).all())

    # (family, version) -> the posts produced under it
    attributed: dict[tuple[str, int], list[Post]] = {}
    for draft in drafts:
        post = posts.get(draft.zernio_post_id or "")
        if post is None:
            continue
        for family, version in (
            (draft.hook_family, draft.hook_version),
            (draft.structure_family, draft.structure_version),
            (draft.visual_family, draft.visual_version),
        ):
            if family and version is not None:
                attributed.setdefault((family, version), []).append(post)

    rows = []
    for template in session.exec(select(Template)).all():
        matched = attributed.get((template.family_id, template.version), [])
        rows.append(
            TemplatePerformance(
                family=template.family_id,
                version=template.version,
                kind=template.kind.value,
                name=template.name,
                status=template.status.value,
                sample_count=len(matched),
                total_engaged_actions=sum(p.engaged_actions for p in matched),
                total_impressions=sum(p.impressions for p in matched),
            )
        )
    return rows
