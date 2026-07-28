from fastapi.testclient import TestClient
from sqlmodel import Session

from app.config import settings
from app.db import get_session
from app.main import app
from app.metrics import template_performance
from app.models.draft import Draft
from app.models.post import Post
from app.models.template import TemplateKind
from app.templates import approve, create_template


def client_with(session: Session) -> TestClient:
    app.dependency_overrides[get_session] = lambda: session
    return TestClient(app)


def attributed(session, n: int, *, engaged: int = 10):
    """A hook with `n` posts attributed to it."""
    hook = create_template(session, kind=TemplateKind.HOOK, name="h", body={})
    structure = create_template(session, kind=TemplateKind.STRUCTURE, name="s", body={})
    approve(session, hook)
    approve(session, structure)
    for index in range(n):
        post = Post(
            zernio_id=f"row-{index}",
            late_post_id=f"late-{index}",
            platform="linkedin",
            engaged_actions=engaged,
            impressions=engaged * 30,
        )
        session.add(post)
        session.add(
            Draft(
                idea="i",
                hook_family=hook.family_id,
                hook_version=1,
                structure_family=structure.family_id,
                structure_version=1,
                zernio_post_id=f"late-{index}",
            )
        )
    session.flush()
    return hook


def hook_row(rows, hook):
    return next(r for r in rows if r.family == hook.family_id)


def test_every_aggregate_carries_its_sample_count(session):
    hook = attributed(session, 3)

    row = hook_row(template_performance(session), hook)

    assert row.sample_count == 3
    assert row.total_engaged_actions == 30


def test_a_thin_sample_is_flagged_as_insufficient(session):
    hook = attributed(session, settings.min_sample_size - 1)

    assert hook_row(template_performance(session), hook).sufficient is False


def test_a_sample_at_the_threshold_is_not_flagged(session):
    hook = attributed(session, settings.min_sample_size)

    assert hook_row(template_performance(session), hook).sufficient is True


def test_a_template_with_no_posts_is_insufficient_not_zero_performing(session):
    """Never generated is a different statement from generated and ignored."""
    hook = create_template(session, kind=TemplateKind.HOOK, name="unused", body={})
    approve(session, hook)

    row = hook_row(template_performance(session), hook)
    assert row.sample_count == 0
    assert row.sufficient is False


def test_the_mean_is_reported_alongside_the_total(session):
    hook = attributed(session, 4, engaged=25)

    row = hook_row(template_performance(session), hook)

    assert row.mean_engaged_actions == 25.0


def test_the_scoreboard_endpoint_exposes_the_sample_count_and_the_flag(session):
    attributed(session, 2)

    rows = client_with(session).get("/metrics/templates").json()

    assert all("sample_count" in r for r in rows)
    assert all("sufficient" in r for r in rows)
    app.dependency_overrides.clear()


def test_the_scoreboard_presents_no_ranking(session):
    """At roughly three posts per template against 12.7x variance, a rank would be noise."""
    attributed(session, 3)

    rows = client_with(session).get("/metrics/templates").json()

    assert all(not any(k in r for k in ("rank", "score", "best", "recommended")) for r in rows)
    app.dependency_overrides.clear()


def test_the_scoreboard_reports_the_threshold_it_is_applying(session):
    attributed(session, 1)

    rows = client_with(session).get("/metrics/templates").json()

    assert all(r["min_sample_size"] == settings.min_sample_size for r in rows)
    app.dependency_overrides.clear()
