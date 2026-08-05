"""The background workflow: what it commits, when, and what refuses to run twice.

**These tests have a database of their own, and that is the whole reason this file exists.**
`conftest.session` hands every other test a session inside a transaction that is rolled back,
and its docstring says what to do if a test ever needs commit behaviour itself: give it its own
database. This one does, because the property under test *is* the commit — a stage another
connection can read while the run is still going. Against the rollback fixture every assertion
here would pass without a single commit ever reaching disk, which is exactly the decoration
`CLAUDE.md` warns about.

The observation is made the only honest way: the fakes the run calls open **their own session
on their own connection** and read the draft back mid-run. A connection outside the run's
transaction can only see what has been committed, so a stage it can name is a stage that
landed. No sleeps and no threads are involved in any of it — `workflow.run` is called
directly, so the sequence is deterministic and there is nothing here that can pass on timing.

The one thread in the design is tested in exactly one place, at the route, with the spawn
recorded rather than started (`conftest.spawned`).
"""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, delete, text
from sqlalchemy.engine import make_url
from sqlmodel import Session, SQLModel, col, select

import app.models  # noqa: F401  — registers every table on SQLModel.metadata
from app import gates, research, workflow
from app.config import settings
from app.db import get_session
from app.deps import get_llm
from app.main import app
from app.models.draft import Draft
from app.models.generation_trace import GenerationTrace
from app.models.research import LIGHT
from app.models.stage import GenerationStage
from app.research import SearchResult
from app.workflow import Resources
from tests.test_studio_workflow import (
    ANGLE_FACT,
    ANGLE_NONE,
    BRIEF,
    READY,
    VERIFIED,
    WRITE_FACT,
    WRITE_NONE,
    fetched,
    templates,
)

# --- a database of this file's own ------------------------------------------------------


@pytest.fixture(scope="session")
def committing_engine():
    """A second Postgres database, created and dropped around this module.

    Schema from `SQLModel.metadata.create_all` rather than from alembic: `alembic check` is
    what pins the models against the migrations, and it runs in the same gate as this file, so
    creating from the models here proves nothing less and takes a second rather than a minute.

    Its own database rather than the shared one with a tidy-up afterwards, and the difference
    is not fastidiousness: a real commit into the database the running app and every other test
    uses would be invisible pollution — `conftest.session` deletes every row at setup, so
    anything left behind would be silently wiped by the next test rather than failing anything.
    """
    url = make_url(settings.database_url)
    name = f"{url.database}_workflow_test"
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    engine = create_engine(url.set(database=name))
    SQLModel.metadata.create_all(engine)
    yield engine
    engine.dispose()
    with admin.connect() as connection:
        connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    admin.dispose()


@pytest.fixture
def committed(committing_engine):
    """An empty database, and a session that really commits into it."""
    with Session(committing_engine) as session:
        for model in reversed(SQLModel.metadata.sorted_tables):
            session.exec(delete(model))
        session.commit()
        yield session


# --- fakes that report what another connection can see ----------------------------------


class Watching:
    """An LLM that answers in order and records the committed stage of each call.

    The stage is read through a **separate session on a separate connection**, which is the
    load-bearing part: it cannot see anything this run has merely flushed, so a stage it names
    is one that was committed before the call it names it for.
    """

    def __init__(self, engine, draft_id: int, *answers, at=None, then=None):
        self.engine = engine
        self.draft_id = draft_id
        self.answers = list(answers)
        self.seen: list[str] = []
        # A call index to interrupt at, and what to do from the outside when it arrives —
        # how the sweep-versus-worker interleaving is produced without any timing at all.
        self.at = at
        self.then = then

    def stage(self) -> str:
        with Session(self.engine) as elsewhere:
            row = elsewhere.get(Draft, self.draft_id)
            return row.generation_stage if row else "gone"

    def complete_json(self, system: str, user: str, images=()) -> dict:
        self.seen.append(self.stage())
        if self.at == len(self.seen) and self.then:
            self.then()
        if not self.answers:
            raise AssertionError("the workflow made an unbounded model call")
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


class WatchingRenderer:
    """The renderer, recording the stage it was called at — that is how `rendering` is seen."""

    def __init__(self, watcher: Watching):
        self.watcher = watcher
        self.seen: list[str] = []

    def screenshot(self, html: str, width: int, height: int) -> bytes:
        self.seen.append(self.watcher.stage())
        return b"PNG"


class NoSearch:
    def search(self, query: str, *, limit: int):
        raise AssertionError("a none-mode workflow must not search")


class Searching:
    """A search provider for the one run below that has a factual floor."""

    def search(self, query: str, *, limit: int):
        return [SearchResult("https://example.com/acme", "Acme checkout")]


RESEARCH_CLAIMS = {
    "claims": [
        {
            "text": "Acme changed its checkout flow to remove one field",
            "citations": [
                {
                    "source": "S1",
                    "span": "Acme changed its checkout flow to remove one field",
                    "stance": "supports",
                }
            ],
        }
    ],
    "unknowns": [],
}
# The finished post's one assertion, resolved against the dossier claim above.
VERIFIED_FACT: dict = {"assertions": []}


class NoImages:
    def generate(self, prompt: str, width: int, height: int) -> bytes:
        raise AssertionError("the template declares html; the image renderer is the wrong one")


def start(session, idea="why clearer product writing matters", **overrides) -> Draft:
    """A claimed draft at `planning`, committed, the way the route leaves one."""
    hook, structure, visual = templates(session)
    session.commit()
    draft, started = workflow.claim(
        session,
        Watching(None, 0),  # never called: every template is named, so nothing is suggested
        key=workflow.claim_key(
            idea=idea,
            hook_id=hook.id,
            structure_id=structure.id,
            visual_id=visual.id,
            asset_values={},
            research_mode=None,
        ),
        idea=idea,
        hook_id=hook.id,
        structure_id=structure.id,
        visual_id=visual.id,
        **overrides,
    )
    assert started
    return draft


def drive(engine, session, draft: Draft, *answers, at=None, then=None):
    """Run the pipeline synchronously, exactly as the thread would, and report what was seen."""
    watcher = Watching(engine, draft.id or 0, *answers, at=at, then=then)
    renderer = WatchingRenderer(watcher)
    workflow.run(
        draft.id or 0,
        resources_=Resources(
            llm=watcher,
            search=NoSearch(),
            html_renderer=renderer,
            image_renderer=NoImages(),
        ),
        session_factory=lambda: Session(engine),
    )
    session.expire_all()
    return watcher, renderer


# --- every state transition -------------------------------------------------------------


def test_every_stage_is_committed_as_it_is_entered(committing_engine, committed):
    """The point of the whole slice: a reader on another connection sees the run move.

    Before this, the stages were written and only flushed, so this list would read
    `["planning", "planning", "planning", "planning", "planning"]` — the value committed when
    the row was created — however far the run had actually got.
    """
    draft = start(committed)

    watcher, renderer = drive(
        committing_engine, committed, draft, BRIEF, ANGLE_NONE, WRITE_NONE, VERIFIED, READY
    )

    # brief, angle, write, verify, rubric — and the renderer after them.
    assert watcher.seen == ["planning", "planning", "drafting", "verifying", "evaluating"]
    assert renderer.seen == ["rendering"]
    with Session(committing_engine) as elsewhere:
        assert elsewhere.get(Draft, draft.id).generation_stage == GenerationStage.READY


def test_the_researching_stage_is_committed_too(committing_engine, committed, monkeypatch):
    """The one transition the `none`-mode run above never enters.

    `researching` is inside `if brief.research_mode != research.NONE`, so a run that makes no
    web request skips both the `advance` and the checkpoint beside it — and that branch is
    where a run spends the longest, so it is the stage a reviewer is most likely to be looking
    at when they wonder whether anything is happening.
    """
    # The dossier's pages come from a stub. Injected by wrapping `run_research` rather than
    # by adding a fetcher argument to `workflow.run`: the worker builds its own adapters
    # precisely so that a caller cannot hand it any, and a test-only parameter on that seam
    # would be a hole in the thing under test. `fetching.fetch` cannot be patched instead —
    # it is a default argument, bound once when `run_research` was defined.
    real_research = research.run_research
    monkeypatch.setattr(
        research,
        "run_research",
        lambda *args, **kwargs: real_research(*args, **{**kwargs, "fetcher": fetched}),
    )
    draft = start(committed, idea="what Acme changed about its checkout flow")
    watcher = Watching(
        committing_engine,
        draft.id or 0,
        BRIEF,
        ANGLE_FACT,
        {"queries": ["acme checkout change"]},
        RESEARCH_CLAIMS,
        WRITE_FACT,
        VERIFIED_FACT,
        READY,
    )
    workflow.run(
        draft.id or 0,
        resources_=Resources(
            llm=watcher,
            search=Searching(),
            html_renderer=WatchingRenderer(watcher),
            image_renderer=NoImages(),
        ),
        session_factory=lambda: Session(committing_engine),
        requested_mode=LIGHT,
    )

    # brief, angle, then the two research calls — both of which are the researching stage.
    assert watcher.seen[:4] == ["planning", "planning", "researching", "researching"]
    assert watcher.seen[4] == "drafting"


def test_a_reload_finds_the_attempt_rather_than_losing_it(committing_engine, committed):
    """Mid-run, `GET /drafts/{id}` has a row to answer with, carrying the idea and the stage.

    A reload used to lose the attempt entirely, because nothing was committed until the run
    finished: there was no row to come back to and no id to come back with.
    """
    draft = start(committed)
    reloaded: dict = {}

    def read_it_back():
        with Session(committing_engine) as elsewhere:
            row = elsewhere.get(Draft, draft.id)
            reloaded.update(
                idea=row.idea, stage=row.generation_stage, hook=row.hook_family is not None
            )

    drive(
        committing_engine,
        committed,
        draft,
        BRIEF,
        ANGLE_NONE,
        WRITE_NONE,
        VERIFIED,
        READY,
        at=3,  # the write call, which is the longest part of a run
        then=read_it_back,
    )

    assert reloaded == {
        "idea": "why clearer product writing matters",
        "stage": "drafting",
        "hook": True,
    }


def test_the_run_releases_its_claim_when_it_finishes(committing_engine, committed):
    """So the same idea, through the same templates, can be generated again tomorrow."""
    draft = start(committed)

    drive(committing_engine, committed, draft, BRIEF, ANGLE_NONE, WRITE_NONE, VERIFIED, READY)

    with Session(committing_engine) as elsewhere:
        assert elsewhere.get(Draft, draft.id).workflow_key is None


# --- failure, and the traces that must outlive it ---------------------------------------


def test_a_failed_stage_is_committed_with_its_reason(committing_engine, committed):
    draft = start(committed)

    drive(committing_engine, committed, draft, BRIEF, ANGLE_NONE, RuntimeError("model down"))

    with Session(committing_engine) as elsewhere:
        stored = elsewhere.get(Draft, draft.id)
        assert stored.generation_stage == GenerationStage.FAILED
        assert "drafting: RuntimeError: model down" in stored.generation_error
        # Released, or Retry could never start a second attempt for the same request.
        assert stored.workflow_key is None


def test_a_failed_stage_keeps_the_trace_rows_of_the_calls_it_paid_for(
    committing_engine, committed
):
    draft = start(committed)

    drive(committing_engine, committed, draft, BRIEF, ANGLE_NONE, RuntimeError("model down"))

    with Session(committing_engine) as elsewhere:
        traces = elsewhere.exec(
            select(GenerationTrace).order_by(col(GenerationTrace.id))
        ).all()
        names = [t.prompt_name for t in traces]
        assert names == ["editorial.brief", "editorial.angle_plan", "draft.write"]
        failed = traces[-1]
        assert failed.error == "RuntimeError: model down"
        assert failed.output_hash is None
        # Never zeroed. `complete_json` returns a parsed dict and drops the usage block, so
        # nothing at this layer has seen a token count — `0` would claim the call was free.
        assert (failed.prompt_tokens, failed.completion_tokens) == (None, None)
        assert failed.model_deployment is None
        assert failed.latency_ms is not None


def test_a_trace_survives_a_failure_nothing_in_the_pipeline_catches(
    committing_engine, committed, monkeypatch
):
    """The property Part 2 turns on, and the one a rollback would quietly destroy.

    `gates.check` runs *outside* every `try` in `generate_reviewed_draft`, so an error there
    escapes the pipeline entirely and lands in the worker's own handler. That handler has to
    commit — a `session.rollback()` there would take the three trace rows for three billed
    calls down with it, and the only record of what this run spent would be the log line.
    """

    def explode(*args, **kwargs):
        raise ValueError("a gate blew up")

    monkeypatch.setattr(gates, "check", explode)
    draft = start(committed)

    drive(committing_engine, committed, draft, BRIEF, ANGLE_NONE, WRITE_NONE)

    with Session(committing_engine) as elsewhere:
        stored = elsewhere.get(Draft, draft.id)
        assert stored.generation_stage == GenerationStage.FAILED
        assert stored.generation_error == "ValueError: a gate blew up"
        traces = elsewhere.exec(select(GenerationTrace)).all()
        assert [t.prompt_name for t in traces] == [
            "editorial.brief",
            "editorial.angle_plan",
            "draft.write",
        ]
        # The write itself succeeded — it is the *run* that failed after it. The distinction
        # is the reason a trace is written after the attempt rather than derived from the
        # draft's final state.
        assert traces[-1].error is None and traces[-1].output_hash is not None


def test_every_trace_of_one_run_shares_the_draft_correlation_id(
    committing_engine, committed
):
    draft = start(committed)

    drive(committing_engine, committed, draft, BRIEF, ANGLE_NONE, WRITE_NONE, VERIFIED, READY)

    with Session(committing_engine) as elsewhere:
        stored = elsewhere.get(Draft, draft.id)
        traces = elsewhere.exec(select(GenerationTrace)).all()
        assert traces
        assert {t.correlation_id for t in traces} == {stored.correlation_id}


# --- the sweep, and the run it is racing ------------------------------------------------


def test_a_swept_run_is_not_resurrected_by_the_worker_that_finishes_afterwards(
    committing_engine, committed
):
    """A slow run declared failed must not come back and say `ready` a minute later.

    The interleaving, produced deterministically: the sweep runs from another connection
    partway through, exactly as a poll would, and the run then carries on. What must not
    happen is the worker committing over the top — a screen that said "failed", a reviewer who
    retried it, and then a draft claiming to be ready with nobody having asked for it.
    """
    draft = start(committed)

    def sweep_from_elsewhere():
        with Session(committing_engine) as poller:
            assert workflow.sweep_stalled(poller, now=datetime.now(UTC) + timedelta(days=1))

    watcher, renderer = drive(
        committing_engine,
        committed,
        draft,
        BRIEF,
        ANGLE_NONE,
        WRITE_NONE,
        VERIFIED,
        READY,
        at=3,
        then=sweep_from_elsewhere,
    )

    with Session(committing_engine) as elsewhere:
        stored = elsewhere.get(Draft, draft.id)
        assert stored.generation_stage == GenerationStage.FAILED
        assert "stopped responding at drafting" in stored.generation_error
        assert stored.workflow_key is None
    # The run stopped rather than ran on: it never reached the rubric, and never drew.
    assert len(watcher.seen) == 3
    assert renderer.seen == []


def test_a_run_that_finishes_first_is_not_swept_out_from_under_itself(
    committing_engine, committed, monkeypatch
):
    """The mirror of the resurrection, and the more damaging direction of the two.

    The sweep decides from a snapshot. A run that was merely slow can commit `ready` and
    release its claim between that SELECT and the UPDATE, and an unconditioned write would then
    stamp `failed` over a finished post: right words, right picture, no longer pushable, and a
    reason saying it stopped responding. The worker's own `SELECT … FOR UPDATE` holds the row
    while it commits, so the sweep is *blocked* there and the window is exactly as wide as the
    commit that closes it — this is not a narrow race.

    Produced deterministically by finishing the run from inside `db.utc`, which the sweep calls
    once per row **after** its SELECT has returned and **before** it writes anything. That is
    the interleaving, statement for statement, with no timing in it.
    """
    draft = start(committed)
    real_utc = workflow.utc
    finished: list[str] = []

    def finish_the_run_first(value):
        if not finished:
            finished.append("done")
            drive(
                committing_engine,
                committed,
                draft,
                BRIEF,
                ANGLE_NONE,
                WRITE_NONE,
                VERIFIED,
                READY,
            )
        return real_utc(value)

    monkeypatch.setattr(workflow, "utc", finish_the_run_first)

    with Session(committing_engine) as poller:
        assert workflow.sweep_stalled(poller, now=datetime.now(UTC) + timedelta(days=1)) == []

    assert finished == ["done"]
    with Session(committing_engine) as elsewhere:
        stored = elsewhere.get(Draft, draft.id)
        assert stored.generation_stage == GenerationStage.READY
        assert stored.generation_error is None


def test_the_sweep_leaves_a_run_inside_the_timeout_alone(committing_engine, committed):
    draft = start(committed)

    with Session(committing_engine) as poller:
        assert workflow.sweep_stalled(poller) == []
        assert poller.get(Draft, draft.id).generation_stage == GenerationStage.PLANNING


# --- these need no commits, so they use the shared rollback fixture ----------------------


def test_a_stalled_run_is_reported_as_failed_on_read(session, client_for):
    """The timeout, at the route that Studio actually polls."""
    draft = start_stalled(session, minutes=settings.workflow_timeout_minutes + 1)

    body = client_for(session).get(f"/drafts/{draft.id}").json()

    assert body["generation_stage"] == "failed"
    assert "stopped responding at researching" in body["generation_error"]
    assert str(settings.workflow_timeout_minutes) in body["generation_error"]


def test_a_run_inside_the_timeout_is_still_reported_as_in_flight(session, client_for):
    draft = start_stalled(session, minutes=settings.workflow_timeout_minutes - 1)

    body = client_for(session).get(f"/drafts/{draft.id}").json()

    assert body["generation_stage"] == "researching"
    assert body["generation_error"] is None


def test_the_sweep_releases_the_claim_so_the_request_can_be_made_again(session):
    draft = start_stalled(session, minutes=settings.workflow_timeout_minutes + 1)
    assert draft.workflow_key

    workflow.sweep_stalled(session)

    assert draft.workflow_key is None


def test_a_terminal_draft_is_never_swept(session):
    """`unreviewed` is terminal too — nothing is going to advance a legacy row."""
    draft = start_stalled(session, minutes=999)
    draft.generation_stage = GenerationStage.UNREVIEWED
    session.add(draft)
    session.commit()

    assert workflow.sweep_stalled(session) == []
    assert draft.generation_stage == GenerationStage.UNREVIEWED


def start_stalled(session, *, minutes: int) -> Draft:
    """An in-flight draft that stopped moving `minutes` ago."""
    hook, structure, visual = templates(session)
    draft = Draft(
        idea="a run whose process went away",
        hook_family=hook.family_id,
        hook_version=hook.version,
        structure_family=structure.family_id,
        structure_version=structure.version,
        visual_family=visual.family_id,
        visual_version=visual.version,
        generation_stage=GenerationStage.RESEARCHING,
        workflow_key="stalled-key",
        created_at=datetime.now(UTC) - timedelta(minutes=minutes),
    )
    session.add(draft)
    session.commit()
    return draft


@pytest.fixture
def client_for(session):
    """A TestClient bound to the test's session, with the LLM stubbed out."""

    def build(bound, llm=None):
        app.dependency_overrides[get_session] = lambda: bound
        app.dependency_overrides[get_llm] = lambda: llm or Watching(None, 0)
        return TestClient(app)

    yield build
    app.dependency_overrides.clear()


# --- duplicate start --------------------------------------------------------------------


def test_a_second_press_of_generate_returns_the_running_draft(session, client_for, spawned):
    """A double-click buys one run, not two. The guard is the UNIQUE index, not this code.

    Mutating the Python here would prove nothing — two sequential requests do not race — so
    the mutation that has to fail this test is dropping the unique constraint, which makes the
    second insert succeed and hands back a different id. Both assertions below are written to
    catch exactly that: the same id, and one draft in the table.
    """
    hook, structure, visual = templates(session)
    session.commit()
    payload = {
        "idea": "one idea, two clicks",
        "hook_id": hook.id,
        "structure_id": structure.id,
        "visual_id": visual.id,
    }
    client = client_for(session)

    first = client.post("/drafts/workflow", json=payload)
    second = client.post("/drafts/workflow", json=payload)

    assert first.status_code == 201 and second.status_code == 201
    assert first.json()["id"] == second.json()["id"]
    assert len(session.exec(select(Draft)).all()) == 1
    # And the second press bought no second run.
    assert spawned == [(first.json()["id"], None)]


def test_a_different_idea_is_not_the_same_request(session, client_for, spawned):
    hook, structure, visual = templates(session)
    session.commit()
    client = client_for(session)
    base = {"hook_id": hook.id, "structure_id": structure.id, "visual_id": visual.id}

    first = client.post("/drafts/workflow", json={"idea": "one thing", **base})
    second = client.post("/drafts/workflow", json={"idea": "another thing", **base})

    assert first.json()["id"] != second.json()["id"]
    assert len(spawned) == 2


def test_the_same_request_may_be_made_again_once_the_run_has_finished(
    session, client_for, spawned
):
    """The claim is released at a terminal stage, so this is a guard and not a ban."""
    hook, structure, visual = templates(session)
    session.commit()
    payload = {
        "idea": "the same idea tomorrow",
        "hook_id": hook.id,
        "structure_id": structure.id,
        "visual_id": visual.id,
    }
    client = client_for(session)
    first = client.post("/drafts/workflow", json=payload).json()

    finished = session.get(Draft, first["id"])
    finished.generation_stage = GenerationStage.READY
    finished.workflow_key = None
    session.add(finished)
    session.commit()

    second = client.post("/drafts/workflow", json=payload).json()

    assert second["id"] != first["id"]
    assert len(spawned) == 2


# --- what the route promises ------------------------------------------------------------


def test_the_route_answers_before_the_run_and_hands_it_to_the_background(
    session, client_for, spawned
):
    hook, structure, visual = templates(session)
    session.commit()

    body = client_for(session).post(
        "/drafts/workflow",
        json={
            "idea": "an idea that takes a minute",
            "hook_id": hook.id,
            "structure_id": structure.id,
            "visual_id": visual.id,
            "research_mode": "none",
        },
    ).json()

    assert body["generation_stage"] == "planning"
    # The lineage is settled in the request, because a draft with no templates is not a thing
    # this schema can hold — `generation.new_reviewed_draft` says why at length.
    assert body["lineage"]["hook"]["name"] == "plain tension"
    assert spawned == [(body["id"], "none")]


def test_retry_starts_a_new_attempt_and_keeps_the_failed_one(session, client_for, spawned):
    failed = start_stalled(session, minutes=1)
    failed.generation_stage = GenerationStage.FAILED
    failed.generation_error = "researching: RuntimeError: search unavailable"
    failed.workflow_key = None
    session.add(failed)
    session.commit()

    body = client_for(session).post(f"/drafts/{failed.id}/retry").json()

    assert body["id"] != failed.id
    assert body["generation_stage"] == "planning"
    assert spawned == [(body["id"], None)]
    # The failed attempt is still there, still saying why. Retry is a second row, never an
    # overwrite: the record of what went wrong is the reason the retry can be audited at all.
    kept = session.get(Draft, failed.id)
    assert kept.generation_stage == "failed"
    assert kept.generation_error == "researching: RuntimeError: search unavailable"
    # Same lineage, resolved through the recorded versions rather than the newest ones.
    assert body["lineage"]["hook"] == {
        "family": kept.hook_family,
        "version": kept.hook_version,
        "name": "plain tension",
    }


def test_a_running_workflow_cannot_be_retried(session, client_for, spawned):
    running = start_stalled(session, minutes=1)

    refused = client_for(session).post(f"/drafts/{running.id}/retry")

    assert refused.status_code == 409
    assert "only a failed workflow" in refused.json()["detail"]
    assert spawned == []


def test_a_ready_workflow_cannot_be_retried(session, client_for, spawned):
    """Retrying a success would buy a second set of billed calls for a draft that is fine."""
    done = start_stalled(session, minutes=1)
    done.generation_stage = GenerationStage.READY
    session.add(done)
    session.commit()

    assert client_for(session).post(f"/drafts/{done.id}/retry").status_code == 409
    assert spawned == []
