import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from sqlmodel import Session, col, select

from app.config import settings
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

    @property
    def sufficient(self) -> bool:
        """Whether this aggregate rests on enough posts to be worth reading.

        False is the normal state early on, and says "not enough evidence yet" — not
        "this template performs badly". The distinction matters: retiring a template on a
        sample of two is how you delete something that works.
        """
        return self.sample_count >= settings.min_sample_size


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


def stamp_published(session: Session) -> int:
    """Stamp `went_live_at` on every pushed draft whose post is live. Returns how many.

    **Scans the whole `post` table, not the ids the sync just touched.** `sync_metrics` sees
    only `fetch_posts()` — Zernio's 50-row analytics window — and 11 published posts in the
    live account have no analytics row at all (`.scratch/v1/seams.md`). Keying detection to
    the window would leave a post that published outside it unstamped forever: the Inbox
    would show it "pushed, awaiting Monte" indefinitely and nothing anywhere would report an
    error. A false negative with no error attached is the failure this pass exists to close.

    Note the re-key. The sync selects posts on `Post.zernio_id`; the draft join is
    `Draft.zernio_post_id == Post.late_post_id` (`draft_for_post`) — a different namespace,
    so the touched-id list is not reusable here even if it were complete.

    The predicate is `status == "published"`, **not the existence of a post row**: the
    analytics payload also carries `partial` and `failed`, and `corpus.py` writes `external`
    for manually added reference rows. Any of those would be a publication that never
    happened.

    ponytail: a re-scan of the unstamped drafts on every sync, not a webhook or an event log.
    The ceiling is latency — up to one sync interval (6h) between a post going live and the
    Inbox knowing — and a scan proportional to unstamped drafts, which is bounded because each
    one leaves the set permanently once stamped. If either ever bites, the upgrade is Zernio's
    own publish callback, not a bigger scan.
    """
    pending = list(
        session.exec(
            select(Draft).where(
                col(Draft.zernio_post_id).is_not(None),
                # Idempotency, and the only thing providing it: an already-stamped draft is
                # never reconsidered, so a re-run cannot move a timestamp that was right.
                col(Draft.went_live_at).is_(None),
            )
        ).all()
    )
    if not pending:
        return 0

    live = {
        post.late_post_id: post
        for post in session.exec(
            select(Post).where(
                col(Post.late_post_id).in_([d.zernio_post_id for d in pending]),
                Post.status == "published",
            )
        ).all()
        if post.late_post_id
    }

    stamped = 0
    for draft in pending:
        post = live.get(draft.zernio_post_id or "")
        if post is None:
            continue
        # The platform's own publication time when Zernio reported one, otherwise the moment
        # of observation — the most that can honestly be claimed about a post found live with
        # no timestamp on it.
        draft.went_live_at = post.published_at or datetime.now(UTC)
        session.add(draft)
        stamped += 1
    session.flush()
    return stamped


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
    # Deliberately not limited to `posts` above. See `stamp_published`.
    went_live = stamp_published(session)

    log.info(
        "metrics sync: %d fetched, %d snapshots, %d newly live",
        len(payloads),
        snapshots,
        went_live,
    )
    return {
        "fetched": len(payloads),
        "snapshots": snapshots,
        "went_live": went_live,
        **result.__dict__,
    }


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
