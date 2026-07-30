"""The four human gates of the lineage circuit, and the ages that make a stall visible.

The circuit has completed zero laps and every one of its gates is invisible until somebody
remembers it, which is what `GET /inbox` is for. These are **queues, not scores** — nothing
here may be asserted, rendered or read as performance.

No clock is faked in this repo: ages are built with real `datetime.now(UTC)` ± `timedelta`, as
`test_metrics.test_record_snapshots_stamps_when_it_was_captured` does. Assertions are therefore
on relative ordering and on floored day counts, never on an exact `now`.
"""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.db import get_session
from app.main import INBOX_LABEL_MAX, app
from app.models.draft import Draft
from app.models.post import Post, Verdict
from app.models.template import TemplateKind, TemplateStatus
from app.templates import approve, create_template, edit_template


@pytest.fixture
def client(session: Session) -> Iterator[TestClient]:
    app.dependency_overrides[get_session] = lambda: session
    yield TestClient(app)
    app.dependency_overrides.clear()


def days_ago(days: float) -> datetime:
    return datetime.now(UTC) - timedelta(days=days)


def a_draft(
    session: Session,
    *,
    idea: str = "the boring acquisition",
    zernio_post_id: str | None = None,
    created_at: datetime | None = None,
    pushed_at: datetime | None = None,
    went_live_at: datetime | None = None,
) -> Draft:
    """A draft placed in an explicit gate. Local to this file — `test_metrics.a_draft` cannot
    set the timestamps every queue here is defined by."""
    hook = create_template(session, kind=TemplateKind.HOOK, name="transformation")
    structure = create_template(session, kind=TemplateKind.STRUCTURE, name="loop")
    approve(session, hook)
    approve(session, structure)
    draft = Draft(
        idea=idea,
        hook_family=hook.family_id,
        hook_version=hook.version,
        structure_family=structure.family_id,
        structure_version=structure.version,
        zernio_post_id=zernio_post_id,
        pushed_at=pushed_at,
        went_live_at=went_live_at,
        created_at=created_at or datetime.now(UTC),
    )
    session.add(draft)
    session.flush()
    return draft


def a_post(
    session: Session,
    *,
    late_post_id: str | None = None,
    content: str = "A post.",
    status: str = "published",
    published_at: datetime | None = None,
    verdict: Verdict | None = None,
) -> Post:
    post = Post(
        zernio_id=f"analytics-row-{late_post_id or content[:12]}",
        late_post_id=late_post_id,
        platform="linkedin",
        content=content,
        status=status,
        published_at=published_at,
        verdict=verdict,
    )
    session.add(post)
    session.flush()
    return post


def a_lap(session: Session, *, live_days_ago: float = 3, verdict: Verdict | None = None):
    """A draft that made it all the way round: built, pushed, published. One lap of the
    circuit, which is what queue 4 is defined over."""
    live = days_ago(live_days_ago)
    draft = a_draft(
        session,
        zernio_post_id="late-live",
        created_at=days_ago(live_days_ago + 2),
        pushed_at=days_ago(live_days_ago + 1),
        went_live_at=live,
    )
    post = a_post(
        session, late_post_id="late-live", published_at=live, verdict=verdict
    )
    return draft, post


def queues(client: TestClient) -> dict:
    return client.get("/inbox").json()


def queues_only(body: dict) -> dict:
    """The four queues, without `closed_circuits`. That field is an int, not a queue, so the
    whole-body sweeps below would read `int["count"]` and raise."""
    return {name: q for name, q in body.items() if name != "closed_circuits"}


def ids(queue: dict) -> list[int]:
    return [item["id"] for item in queue["items"]]


# --- membership, one queue at a time -------------------------------------------------------


def test_a_proposed_template_is_awaiting_review(client, session):
    proposed = create_template(session, kind=TemplateKind.HOOK, name="transformation")

    queue = queues(client)["proposals_awaiting_review"]

    assert ids(queue) == [proposed.id]
    assert queue["items"][0]["label"] == "transformation"


def test_an_approved_or_retired_template_is_not_awaiting_review(client, session):
    """The gate is review, not existence. Approved passed it; retired was withdrawn."""
    approved = create_template(session, kind=TemplateKind.HOOK, name="approved")
    approve(session, approved)
    retired = create_template(session, kind=TemplateKind.STRUCTURE, name="retired")
    from app.templates import retire

    retire(session, retired)

    assert queues(client)["proposals_awaiting_review"]["count"] == 0


def test_a_superseded_proposal_is_not_awaiting_review(client, session):
    """`edit_template` carries the status forward, so revising a proposal leaves two PROPOSED
    rows in one family. Only the current version is reviewable — approving v1 would approve
    wording that has already been replaced, so a bare `status == PROPOSED` over the whole
    table is the wrong query and this is what pins it."""
    original = create_template(session, kind=TemplateKind.HOOK, name="v1")
    revised = edit_template(session, original, name="v2")

    queue = queues(client)["proposals_awaiting_review"]

    assert ids(queue) == [revised.id]
    assert queue["items"][0]["label"] == "v2"


def test_a_draft_that_never_left_the_building_is_awaiting_push(client, session):
    draft = a_draft(session)

    queue = queues(client)["built_awaiting_push"]

    assert ids(queue) == [draft.id]
    assert queue["items"][0]["label"] == "the boring acquisition"


def test_a_pushed_draft_is_awaiting_monte(client, session):
    draft = a_draft(session, zernio_post_id="late-1", pushed_at=days_ago(6))

    queue = queues(client)["pushed_awaiting_monte"]

    assert ids(queue) == [draft.id]


def test_a_published_draft_is_awaiting_a_verdict(client, session):
    _, post = a_lap(session)

    queue = queues(client)["published_awaiting_verdict"]

    assert ids(queue) == [post.id]


def test_a_ruled_post_has_left_the_inbox(client, session):
    """A verdict is the last gate. Once it is recorded there is nothing left to wait for."""
    a_lap(session, verdict=Verdict.WORKED)

    body = queues(client)
    assert body["published_awaiting_verdict"]["count"] == 0
    assert body["pushed_awaiting_monte"]["count"] == 0
    assert body["built_awaiting_push"]["count"] == 0


def test_an_empty_inbox_reports_four_empty_queues_not_an_absence(client, session):
    body = queues(client)

    assert set(body) == {
        "proposals_awaiting_review",
        "built_awaiting_push",
        "pushed_awaiting_monte",
        "published_awaiting_verdict",
        # Not a queue: nothing is waiting behind it. It is the count of laps already
        # finished, and on an empty database that count is 0 rather than absent.
        "closed_circuits",
    }
    assert all(q == {"count": 0, "items": []} for q in queues_only(body).values())
    assert body["closed_circuits"] == 0


# --- the queues do not overlap -------------------------------------------------------------


def test_a_draft_is_in_exactly_one_queue(client, session):
    """`pushed_at` and `went_live_at` partition the drafts, which is why there is no status
    column: a third source of truth could disagree with these two. Three drafts, one per
    gate, and each appears once across the whole response."""
    built = a_draft(session, idea="built")
    pushed = a_draft(session, idea="pushed", zernio_post_id="late-1", pushed_at=days_ago(4))
    live, post = a_lap(session)

    body = queues(client)

    assert ids(body["built_awaiting_push"]) == [built.id]
    assert ids(body["pushed_awaiting_monte"]) == [pushed.id]
    assert ids(body["published_awaiting_verdict"]) == [post.id]
    # The live draft's own id appears in no draft queue — it has passed both of them.
    assert live.id not in ids(body["built_awaiting_push"])
    assert live.id not in ids(body["pushed_awaiting_monte"])


def test_a_pushed_draft_with_no_post_yet_stays_awaiting_monte(client, session):
    """Pushed but never published is the normal state, not a failure. It must not fall through
    into the verdict queue, and it must not vanish."""
    draft = a_draft(session, zernio_post_id="late-never", pushed_at=days_ago(9))

    body = queues(client)

    assert ids(body["pushed_awaiting_monte"]) == [draft.id]
    assert body["published_awaiting_verdict"]["count"] == 0


# --- queue 4 is lineage-only ---------------------------------------------------------------


def test_a_published_post_with_no_draft_behind_it_is_not_a_queue(client, session):
    """**The deliberate scoping choice.** Postgres holds 57 published posts, all without a
    verdict, and zero drafts have ever gone live. "All published posts" would open this page
    with a 57-row backlog of writing that predates the app — none of it a gate anyone intends
    to clear — burying the circuit the Inbox exists to make visible.

    `POST /posts/{id}/verdict` still accepts any post id, so ruling on one of these remains
    possible. It is simply not something waiting on a human.
    """
    a_post(session, content="Monte wrote this by hand, months ago.", published_at=days_ago(90))

    assert queues(client)["published_awaiting_verdict"]["count"] == 0


def test_a_post_whose_draft_is_only_pushed_is_not_awaiting_a_verdict(client, session):
    """A post row exists and carries no verdict, but its draft was never observed live, so
    there is nothing to rule on yet. The gate is `went_live_at`, not row existence — the same
    predicate distinction `test_publish_detection` pins on the other side."""
    a_draft(session, zernio_post_id="late-1", pushed_at=days_ago(2))
    a_post(session, late_post_id="late-1", published_at=days_ago(1))

    assert queues(client)["published_awaiting_verdict"]["count"] == 0


def test_the_lineage_join_is_on_late_post_id(client, session):
    """`Draft.zernio_post_id == Post.late_post_id`. `Post.zernio_id` is a different identifier
    entirely and matching on it finds nothing, so a post carrying the id in the wrong column
    must not join.

    A correctly-linked lap is seeded alongside so the queue is non-empty: without it this test
    would pass just as well against a query that returned nothing at all.
    """
    _, linked = a_lap(session)
    crossed = a_post(session, late_post_id=None, content="Crossed ids.", published_at=days_ago(1))
    crossed.zernio_id = "late-live"
    session.flush()
    a_draft(session, zernio_post_id="late-crossed", went_live_at=days_ago(1))

    queue = queues(client)["published_awaiting_verdict"]

    assert ids(queue) == [linked.id]


# --- closed circuits -------------------------------------------------------------------------


def test_a_verdict_on_a_post_with_lineage_closes_a_circuit(client, session):
    """The one number on this page that is not a queue: laps the circuit has actually
    completed — generate, push, publish, rule.

    `a_lap` builds the first three, so the verdict is the only thing missing, and it is set
    through the real route rather than written onto the column: the counter has to move for an
    operator doing the ordinary thing, not for a test that reaches past the API.

    Retraction is the same predicate read backwards and belongs here rather than in a test of
    its own — US-006 lets a ruling be cleared, and a lap that stops being ruled on stops being
    closed. A counter that only ever went up would be a second source of truth about the same
    column.
    """
    _, post = a_lap(session)

    assert queues(client)["closed_circuits"] == 0

    ruled = client.post(f"/posts/{post.id}/verdict", json={"verdict": "worked", "note": "Landed."})

    assert ruled.status_code == 200
    assert queues(client)["closed_circuits"] == 1

    client.post(f"/posts/{post.id}/verdict", json={"verdict": None, "note": ""})

    assert queues(client)["closed_circuits"] == 0


def test_a_ruled_post_with_no_draft_behind_it_closes_no_circuit(client, session):
    """A verdict is not a lap. Ruling on one of the 57 posts that predate this app records a
    judgement about writing the circuit never touched — counting it would report a loop that
    has run zero times as having run once, which is the exact claim this number exists to
    refuse. Same lineage-only scoping as queue 4, and the same join.
    """
    a_post(
        session,
        content="Monte wrote this by hand, months ago.",
        published_at=days_ago(90),
        verdict=Verdict.WORKED,
    )

    assert queues(client)["closed_circuits"] == 0


def test_a_live_draft_still_awaiting_a_verdict_closes_no_circuit(client, session):
    """The lap is not closed until it is ruled on, which is why the counter and queue 4 are
    complements over the same join rather than the same query: the post below is in the queue
    *because* it is not in this count."""
    _, post = a_lap(session)

    body = queues(client)

    assert ids(body["published_awaiting_verdict"]) == [post.id]
    assert body["closed_circuits"] == 0


# --- counts agree with the items -----------------------------------------------------------


def test_every_count_equals_the_number_of_items_it_reports(client, session):
    """A count derived from anything but the rows returned could disagree with them, and the
    number is what an operator reads first."""
    for name in ("a", "b", "c"):
        create_template(session, kind=TemplateKind.HOOK, name=name)
    a_draft(session, idea="one")
    a_draft(session, idea="two")
    a_draft(session, zernio_post_id="late-1", pushed_at=days_ago(1))
    a_lap(session)

    body = queues(client)

    counted = [(q["count"], len(q["items"])) for q in queues_only(body).values()]
    assert counted == [(3, 3), (2, 2), (1, 1), (1, 1)]


# --- ages ----------------------------------------------------------------------------------


def test_every_item_carries_an_age(client, session):
    """An age is the point. A queue that says "waiting 6 days" tells an operator something a
    count never will."""
    a_draft(session, created_at=days_ago(6.5))

    item = queues(client)["built_awaiting_push"]["items"][0]

    assert item["age_days"] == 6
    assert item["waiting_since"] is not None


def test_an_age_is_measured_from_the_gate_the_item_is_waiting_at(client, session):
    """Not from creation. A draft built 20 days ago and pushed yesterday has been waiting on
    Monte for one day, and reporting 20 would make a healthy queue look abandoned."""
    a_draft(
        session,
        zernio_post_id="late-1",
        created_at=days_ago(20),
        pushed_at=days_ago(1.2),
    )

    assert queues(client)["pushed_awaiting_monte"]["items"][0]["age_days"] == 1


def test_the_oldest_item_comes_first(client, session):
    newest = a_draft(session, idea="newest", created_at=days_ago(1))
    oldest = a_draft(session, idea="oldest", created_at=days_ago(30))
    middle = a_draft(session, idea="middle", created_at=days_ago(9))

    queue = queues(client)["built_awaiting_push"]

    assert ids(queue) == [oldest.id, middle.id, newest.id]
    assert [i["age_days"] for i in queue["items"]] == [30, 9, 1]


def test_an_item_younger_than_a_day_reports_zero_rather_than_failing(client, session):
    a_draft(session, created_at=days_ago(0.25))

    assert queues(client)["built_awaiting_push"]["items"][0]["age_days"] == 0


def test_a_pushed_draft_with_no_pushed_at_still_gets_an_age(client, session):
    """`push_draft` writes `zernio_post_id` and `pushed_at` together, but the column is nullable
    and rows predating it exist. A blank age is the one thing this response cannot render, so
    it falls back to when the draft was built — which can only overstate the wait, never
    understate it."""
    a_draft(session, zernio_post_id="late-1", created_at=days_ago(11), pushed_at=None)

    assert queues(client)["pushed_awaiting_monte"]["items"][0]["age_days"] == 11


def test_a_long_post_is_labelled_not_reproduced(client, session):
    """LinkedIn posts here average ~818 characters. Four queues of that is prose with the
    queue buried in it."""
    _, post = a_lap(session)
    post.content = "word " * 400
    session.flush()

    label = queues(client)["published_awaiting_verdict"]["items"][0]["label"]

    assert len(label) == INBOX_LABEL_MAX
    assert label.endswith("…")


# --- the two gaps this slice closed --------------------------------------------------------


def test_pushed_at_and_created_at_reach_the_api(client, session):
    """`DraftOut` is hand-mapped in two places, so a column can exist, be written and still be
    invisible — and without these two the Inbox cannot render "days waiting" at all.

    Asserted on the serialised body, because the model is exactly what would still be right
    while the response was wrong.
    """
    draft = a_draft(session, zernio_post_id="late-1", pushed_at=days_ago(3))

    body = client.get(f"/drafts/{draft.id}").json()

    assert body["pushed_at"] is not None
    assert body["created_at"] is not None
    listed = client.get("/drafts").json()[0]
    assert (listed["pushed_at"], listed["created_at"]) == (body["pushed_at"], body["created_at"])


def test_an_unpushed_draft_reports_a_null_pushed_at_and_a_real_created_at(client, session):
    draft = a_draft(session)

    body = client.get(f"/drafts/{draft.id}").json()

    assert body["pushed_at"] is None
    assert body["created_at"] is not None


def test_templates_can_be_filtered_to_what_is_awaiting_review(client, session):
    """The gap: `latest_versions` is status-blind and `usable_only` is APPROVED-only, so no
    query yielded proposed-awaiting-review before this parameter."""
    proposed = create_template(session, kind=TemplateKind.HOOK, name="proposed")
    approved = create_template(session, kind=TemplateKind.HOOK, name="approved")
    approve(session, approved)

    listed = client.get("/templates?status=proposed").json()

    assert [t["id"] for t in listed] == [proposed.id]
    assert [t["name"] for t in client.get("/templates?status=approved").json()] == ["approved"]


def test_the_status_filter_composes_with_kind(client, session):
    create_template(session, kind=TemplateKind.HOOK, name="a hook")
    create_template(session, kind=TemplateKind.STRUCTURE, name="a structure")

    listed = client.get("/templates?kind=structure&status=proposed").json()

    assert [t["name"] for t in listed] == ["a structure"]


def test_the_status_filter_agrees_with_the_inbox(client, session):
    """One definition of "awaiting review", so the page and the list endpoint cannot drift."""
    create_template(session, kind=TemplateKind.HOOK, name="proposed")
    original = create_template(session, kind=TemplateKind.VISUAL, name="v1")
    edit_template(session, original, name="v2")
    approve(session, create_template(session, kind=TemplateKind.STRUCTURE, name="approved"))

    listed = client.get("/templates?status=proposed").json()
    queue = queues(client)["proposals_awaiting_review"]

    assert sorted(t["id"] for t in listed) == sorted(ids(queue))


def test_an_unknown_status_is_rejected_rather_than_matching_nothing(client, session):
    """A typo that returned an empty list would read as an empty review queue — the exact
    silent wrongness `SORTABLE` refuses for `/posts`."""
    response = client.get("/templates?status=pending")

    assert response.status_code == 422
    detail = str(response.json()["detail"])
    assert all(s.value in detail for s in TemplateStatus)


def test_status_and_usable_only_together_are_refused(client, session):
    """`usable_only` already means `status=approved`. Honouring both would silently drop one,
    and a dropped `status=proposed` returns approved templates while claiming to be a review
    queue."""
    response = client.get("/templates?kind=hook&status=proposed&usable_only=true")

    assert response.status_code == 422
    assert "usable_only" in str(response.json()["detail"])
