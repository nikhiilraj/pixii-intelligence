from datetime import UTC, datetime, timedelta

from sqlmodel import func, select

from app.metrics import (
    draft_for_post,
    record_snapshots,
    sync_metrics,
    template_performance,
)
from app.models.draft import Draft
from app.models.metric import MetricSnapshot
from app.models.post import Post
from app.models.template import TemplateKind
from app.templates import approve, create_template


class FakeZernio:
    """Serves canned analytics payloads, one per call."""

    def __init__(self, *payloads: list[dict]):
        self.payloads = list(payloads)
        self.calls = 0

    def fetch_posts(self, platform: str | None = None) -> list[dict]:
        self.calls += 1
        return self.payloads[min(self.calls - 1, len(self.payloads) - 1)]


def analytics_post(late_id: str, *, likes=10, comments=2, shares=1, saves=0, impressions=500):
    """An analytics row. Note `_id` and `latePostId` differ, exactly as the live API returns."""
    return {
        "_id": f"analytics-row-{late_id}",
        "latePostId": late_id,
        "platform": "linkedin",
        "content": "A post.",
        "publishedAt": "2026-07-28T14:04:10.211Z",
        "status": "published",
        "analytics": {
            "impressions": impressions,
            "reach": impressions - 50,
            "likes": likes,
            "comments": comments,
            "shares": shares,
            "saves": saves,
            "engagementRate": 1.5,
        },
    }


def a_draft(session, zernio_post_id: str | None) -> Draft:
    hook = create_template(session, kind=TemplateKind.HOOK, name="transformation", body={})
    structure = create_template(session, kind=TemplateKind.STRUCTURE, name="loop", body={})
    approve(session, hook)
    approve(session, structure)
    draft = Draft(
        idea="i",
        hook_family=hook.family_id,
        hook_version=hook.version,
        structure_family=structure.family_id,
        structure_version=structure.version,
        zernio_post_id=zernio_post_id,
    )
    session.add(draft)
    session.flush()
    return draft, hook, structure


def test_a_sync_pulls_metrics_and_records_a_snapshot_per_post(session):
    zernio = FakeZernio([analytics_post("late-1"), analytics_post("late-2")])

    sync_metrics(session, zernio)

    assert session.exec(select(func.count()).select_from(MetricSnapshot)).one() == 2


def test_snapshots_accumulate_rather_than_overwrite(session):
    """A post's engagement moves over time. Overwriting would destroy that curve."""
    zernio = FakeZernio(
        [analytics_post("late-1", likes=10)],
        [analytics_post("late-1", likes=40)],
    )

    sync_metrics(session, zernio)
    sync_metrics(session, zernio)

    snapshots = session.exec(select(MetricSnapshot).order_by(MetricSnapshot.captured_at)).all()
    assert [s.likes for s in snapshots] == [10, 40]


def test_the_post_row_itself_carries_the_latest_numbers(session):
    zernio = FakeZernio(
        [analytics_post("late-1", likes=10)],
        [analytics_post("late-1", likes=40)],
    )

    sync_metrics(session, zernio)
    sync_metrics(session, zernio)

    post = session.exec(select(Post)).one()
    assert post.likes == 40


def test_engaged_actions_is_the_sum_of_likes_comments_shares_and_saves(session):
    zernio = FakeZernio([analytics_post("late-1", likes=10, comments=4, shares=2, saves=1)])

    sync_metrics(session, zernio)

    assert session.exec(select(MetricSnapshot)).one().engaged_actions == 17


def test_a_draft_is_joined_to_its_post_through_late_post_id(session):
    """The create-time id appears in analytics as latePostId, never as analytics._id."""
    draft, _, _ = a_draft(session, zernio_post_id="late-1")
    sync_metrics(session, FakeZernio([analytics_post("late-1")]))

    post = draft_for_post_reverse(session, draft)

    assert post is not None
    assert post.late_post_id == "late-1"


def draft_for_post_reverse(session, draft):
    return session.exec(select(Post).where(Post.late_post_id == draft.zernio_post_id)).first()


def test_joining_on_the_analytics_row_id_finds_nothing(session):
    """Guards the exact mistake that would silently attribute zero to every template."""
    a_draft(session, zernio_post_id="late-1")
    sync_metrics(session, FakeZernio([analytics_post("late-1")]))

    post = session.exec(select(Post)).one()
    assert post.zernio_id == "analytics-row-late-1"
    assert post.late_post_id == "late-1"
    assert post.zernio_id != post.late_post_id


def test_a_post_finds_the_draft_that_produced_it(session):
    draft, _, _ = a_draft(session, zernio_post_id="late-1")
    sync_metrics(session, FakeZernio([analytics_post("late-1")]))
    post = session.exec(select(Post)).one()

    assert draft_for_post(session, post) is not None
    assert draft_for_post(session, post).id == draft.id


def test_a_post_nobody_generated_has_no_draft(session):
    sync_metrics(session, FakeZernio([analytics_post("organic-1")]))
    post = session.exec(select(Post)).one()

    assert draft_for_post(session, post) is None


def test_a_post_with_no_late_post_id_matches_no_draft_at_all(session):
    """The guard at the top of `draft_for_post`, which nothing exercised.

    Without it the query becomes `zernio_post_id == None`, and an unpushed draft — every draft
    in Studio before someone presses Push — has exactly that. A post that reached the corpus
    without a `latePostId` would then be credited with whichever draft nobody has pushed yet:
    a lineage attribution invented out of two nulls, on the surface whose only job is
    attribution.
    """
    a_draft(session, zernio_post_id=None)  # never pushed
    sync_metrics(session, FakeZernio([analytics_post("organic-1")]))
    post = session.exec(select(Post)).one()
    post.late_post_id = None

    assert draft_for_post(session, post) is None


def test_template_performance_counts_only_posts_with_lineage(session):
    draft, hook, _ = a_draft(session, zernio_post_id="late-1")
    sync_metrics(
        session,
        FakeZernio([analytics_post("late-1", likes=20), analytics_post("organic-1", likes=999)]),
    )

    rows = template_performance(session)

    hook_row = next(r for r in rows if r.family == hook.family_id)
    assert hook_row.sample_count == 1
    assert hook_row.total_engaged_actions == 23


def test_template_performance_reports_a_sample_count_beside_every_aggregate(session):
    draft, hook, _ = a_draft(session, zernio_post_id="late-1")
    sync_metrics(session, FakeZernio([analytics_post("late-1")]))

    rows = template_performance(session)

    assert all(hasattr(r, "sample_count") for r in rows)
    assert all(r.sample_count >= 0 for r in rows)


def test_a_pushed_draft_with_no_metrics_yet_is_not_an_error(session):
    """Analytics excludes unpublished drafts, so this is the normal state after a push."""
    a_draft(session, zernio_post_id="not-published-yet")

    sync_metrics(session, FakeZernio([]))

    rows = template_performance(session)
    assert all(r.sample_count == 0 for r in rows)


def test_performance_is_keyed_on_family_and_version_not_row_id(session):
    """A later template edit must not move history onto the new version."""
    from app.templates import edit_template

    draft, hook, _ = a_draft(session, zernio_post_id="late-1")
    sync_metrics(session, FakeZernio([analytics_post("late-1")]))
    edit_template(session, hook, name="renamed")

    rows = template_performance(session)
    original = next(r for r in rows if r.family == hook.family_id and r.version == 1)
    revised = next(r for r in rows if r.family == hook.family_id and r.version == 2)

    assert original.sample_count == 1
    assert revised.sample_count == 0


def test_record_snapshots_stamps_when_it_was_captured(session):
    before = datetime.now(UTC) - timedelta(seconds=1)
    post = Post(zernio_id="a", late_post_id="late-1", platform="linkedin", likes=3)
    session.add(post)
    session.flush()

    record_snapshots(session, [post])

    snapshot = session.exec(select(MetricSnapshot)).one()
    assert snapshot.captured_at.replace(tzinfo=UTC) >= before
