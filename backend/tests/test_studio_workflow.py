"""The connected Studio path, from editorial artifacts through review and rendering."""

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from app.db import get_session
from app.deps import get_html_renderer, get_llm, get_search
from app.fetching import Fetched
from app.generation import generate_reviewed_draft
from app.main import app
from app.models.draft import Draft
from app.models.research import DEEP, LIGHT
from app.models.template import TemplateKind
from app.research import SearchResult
from app.templates import approve, create_template

BRIEF = {
    "objective": "Help operators explain one useful product decision.",
    "audience": "product operators responsible for checkout",
    "desired_action": "review one unnecessary checkout step",
    "constraints": [],
}
ANGLE_NONE = {
    "thesis": "clearer product writing improves internal decisions",
    "tension": "teams confuse more detail with more clarity",
    "audience_stake": "operators spend less time resolving avoidable ambiguity",
    "claims": [{"text": "clearer product writing helps teams make decisions"}],
    "beats": ["name the ambiguity", "show the editing principle"],
    "cta": "remove one sentence that does not change the decision",
}
ANGLE_FACT = {
    "thesis": "the checkout change is worth studying",
    "tension": "small flow changes are easy to dismiss",
    "audience_stake": "operators can inspect a concrete decision",
    "claims": [{"text": "Acme changed its checkout flow to remove one field"}],
    "beats": ["state the change", "explain the implication"],
    "cta": "review one field in your checkout",
}
BODY = (
    "Most teams add detail when a decision feels unclear. The better move is often deletion. "
    "Remove the sentence that changes no choice, then ask whether the next action is obvious."
)
WRITE_NONE = {
    "hook": "Clarity is often subtraction.",
    "body": BODY,
    "visual_values": {},
}
WRITE_FACT = {
    "hook": "One checkout field was doing no useful work.",
    "body": (
        "Acme changed its checkout flow to remove one field [S1]. That is a useful prompt to "
        "inspect the fields customers complete and ask which decision each one supports."
    ),
    "visual_values": {},
}
# What a verifier says about a post asserting nothing it has to stand behind. Queued on
# every run below that reaches readiness: verification sits between the write and the
# rubric, and `QueuedLLM` refuses a call it has no answer for. The runs that stop at a
# gate never reach it, which is why only some of the queues below carry it.
VERIFIED: dict = {"assertions": []}
READY = {"deductions": []}


class QueuedLLM:
    def __init__(self, *answers: dict):
        self.answers = list(answers)
        self.calls: list[tuple[str, str]] = []

    def complete_json(self, system: str, user: str, images=()) -> dict:
        self.calls.append((system, user))
        if not self.answers:
            raise AssertionError("workflow made an unbounded model call")
        return self.answers.pop(0)


class Search:
    def search(self, query: str, *, limit: int):
        return [SearchResult("https://example.com/acme", "Acme checkout")]


class Renderer:
    def __init__(self, error: Exception | None = None):
        self.error = error

    def screenshot(self, html: str, width: int, height: int) -> bytes:
        if self.error:
            raise self.error
        return b"PNG"


class ImageRenderer:
    """The workflow's second renderer. The visual below declares `html`, so nothing here
    should ever reach this — `b"AI"` arriving on a draft would say the renderer was picked
    from something other than the template. `tests/test_renderer_selection.py` is where that
    choice is exercised on purpose."""

    def generate(self, prompt: str, width: int, height: int) -> bytes:
        return b"AI"


def fetched(_: str) -> Fetched:
    text = "Acme changed its checkout flow to remove one field after reviewing support use."
    return Fetched(
        url="https://example.com/acme",
        title="Acme checkout",
        text=text,
        content_type="text/html",
        content_hash="a" * 64,
        fetched_at=datetime.now(UTC),
    )


def templates(session):
    hook = create_template(
        session,
        kind=TemplateKind.HOOK,
        name="plain tension",
        body={"pattern": "short tension", "tone": "plain"},
    )
    structure = create_template(
        session,
        kind=TemplateKind.STRUCTURE,
        name="argument",
        body={"sections": [{"name": "case", "guidance": "make one argument"}]},
    )
    visual = create_template(
        session,
        kind=TemplateKind.VISUAL,
        name="textless",
        body={"renderer": "html", "html": "<p>Pixii</p>"},
        slots=[],
    )
    for template in (hook, structure, visual):
        approve(session, template)
    return hook, structure, visual


def run(session, llm, *, idea, angle, requested_mode=None, renderer=None):
    hook, structure, visual = templates(session)
    return generate_reviewed_draft(
        session,
        llm,
        Search(),
        renderer or Renderer(),
        ImageRenderer(),
        idea=idea,
        hook_id=hook.id,
        structure_id=structure.id,
        visual_id=visual.id,
        requested_mode=requested_mode,
        research_fetcher=fetched,
    )


def test_no_research_still_plans_and_reaches_ready(session):
    llm = QueuedLLM(BRIEF, ANGLE_NONE, WRITE_NONE, VERIFIED, READY)
    draft = run(session, llm, idea="why clearer product writing matters", angle=ANGLE_NONE)

    assert draft.generation_stage == "ready"
    assert draft.editorial_brief_id and draft.angle_plan_id
    assert draft.research_job_id is None
    assert draft.gate_results == []
    assert draft.readiness_result["decision"] == "ready_for_editorial_review"
    assert draft.visual_image == b"PNG"


@pytest.mark.parametrize("mode", [LIGHT, DEEP])
def test_factual_flow_researches_before_writing_and_persists_dossier(session, mode):
    research_answer = {
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
    llm = QueuedLLM(
        BRIEF,
        ANGLE_FACT,
        {"queries": ["Acme checkout flow field"]},
        research_answer,
        WRITE_FACT,
        VERIFIED,
        READY,
    )
    draft = run(
        session,
        llm,
        idea="What changed in Acme checkout?",
        angle=ANGLE_FACT,
        requested_mode=mode,
    )

    assert draft.generation_stage == "ready"
    assert draft.research_job_id is not None
    assert draft.gate_results == []
    # The dossier reached the *writer*. Found by which prompt was sent rather than by counting
    # back from the end of the call list: verification now sits between the write and the
    # rubric, and an index quietly starts asserting about a different prompt every time a
    # stage is added between them.
    write = next(
        user for system, user in llm.calls if system.startswith("You write a source-disciplined")
    )
    assert "Sources:" in write


def test_research_floor_is_never_silently_lowered(session):
    draft = run(
        session,
        QueuedLLM(BRIEF),
        idea="What changed in Acme checkout?",
        angle=ANGLE_FACT,
        requested_mode="none",
    )

    assert draft.generation_stage == "failed"
    assert "ModeBelowFloor" in (draft.generation_error or "")
    assert "at least 'light'" in (draft.generation_error or "")


# --- the depth control on the wire ------------------------------------------------------------
#
# Three routes take `research_mode` and all three refuse a below-floor request at the door, so
# the refusal is exercised once per route rather than once in total: the failure mode is a route
# that forgot to ask, and a shared helper tested on its own would pass while two routes ignored
# it. Above the floor is a request the operator is allowed to make and is checked in both
# directions — that it reaches the run, and that it is what the brief records afterwards.


def _client(session, llm=None, search=None):
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_llm] = lambda: llm or QueuedLLM()
    app.dependency_overrides[get_search] = search or Search
    app.dependency_overrides[get_html_renderer] = lambda: Renderer()
    return TestClient(app)


def test_a_depth_below_the_floor_is_refused_before_any_draft_exists(session, spawned):
    """The refusal names what was asked, the floor, and what the detector saw.

    All three as fields rather than only inside the sentence: the detector is a heuristic, so
    "what did it see" is the first question about a surprising floor, and a client composing
    that answer out of the message would be parsing prose.
    """
    hook, structure, visual = templates(session)
    try:
        response = _client(session).post(
            "/drafts/workflow",
            json={
                "idea": "What changed in Acme checkout?",
                "hook_id": hook.id,
                "structure_id": structure.id,
                "visual_id": visual.id,
                "research_mode": "none",
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert detail["requested_mode"] == "none"
    assert detail["recommended_mode"] == "light"
    assert "organisation" in detail["mode_signals"]
    assert "at least 'light'" in detail["error"]
    # Nothing was written and nothing was started. The failure this replaces is not a 502 — it
    # is a `failed` row per attempt, saying `planning: ModeBelowFloor`, for a request that was
    # refusable before a draft existed.
    assert session.exec(select(Draft)).all() == []
    assert spawned == []


def test_a_depth_above_the_floor_reaches_the_run(session, spawned):
    """Raising is the operator's to do, and `start` is where it has to arrive.

    The mode travels to a background thread, so "the route accepted it" and "the run got it"
    are two different claims; `spawned` is the second one.
    """
    hook, structure, visual = templates(session)
    try:
        response = _client(session).post(
            "/drafts/workflow",
            json={
                "idea": "why clearer product writing matters",
                "hook_id": hook.id,
                "structure_id": structure.id,
                "visual_id": visual.id,
                "research_mode": DEEP,
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 201, response.text
    assert spawned == [(response.json()["id"], DEEP)]


def test_the_brief_reports_what_was_asked_the_floor_and_what_ran(session):
    """The response half of the contract, and it is three fields because it has to be.

    One field would make "the operator asked for deep and the run did light" unanswerable —
    which is the same reason `ResearchJob` stores `recommended_mode` beside `mode`. Studio
    prints all three plus the signals.
    """
    llm = QueuedLLM(
        BRIEF,
        ANGLE_NONE,
        {"queries": ["clearer product writing"]},
        {"claims": [], "unknowns": []},
        WRITE_NONE,
        VERIFIED,
        READY,
    )
    draft = run(
        session,
        llm,
        idea="why clearer product writing matters",
        angle=ANGLE_NONE,
        requested_mode=DEEP,
    )
    session.commit()
    app.dependency_overrides[get_session] = lambda: session
    try:
        brief = TestClient(app).get(f"/drafts/{draft.id}").json()["editorial"]["brief"]
    finally:
        app.dependency_overrides.clear()

    assert brief["requested_mode"] == DEEP
    # The floor for this idea is `none`; the run went above it because it was asked to.
    assert brief["recommended_mode"] == "none"
    assert brief["research_mode"] == DEEP
    assert brief["mode_signals"] == []


def test_the_prompts_that_ran_include_the_two_no_artifact_carries(session):
    """Research and revision write no prompt columns anywhere else.

    The brief, the angle, the write and the rubric each stamp their own prompt onto the row
    they produce, so those four were already answerable. `research.queries`, `research.claims`
    and `revision.targeted` produce a `ResearchJob` and a count, neither of which has anywhere
    to record which prompt spoke — and the trace rows that do know had no route reading them.
    Remove `prompts` from the lineage and this is what notices.
    """
    llm = QueuedLLM(
        BRIEF,
        ANGLE_FACT,
        {"queries": ["Acme checkout flow field"]},
        {
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
        },
        WRITE_FACT,
        VERIFIED,
        READY,
    )
    draft = run(
        session,
        llm,
        idea="What changed in Acme checkout?",
        angle=ANGLE_FACT,
        requested_mode=LIGHT,
    )
    session.commit()
    app.dependency_overrides[get_session] = lambda: session
    try:
        prompts = TestClient(app).get(f"/drafts/{draft.id}").json()["editorial"]["prompts"]
    finally:
        app.dependency_overrides.clear()

    names = [entry["name"] for entry in prompts]
    assert "research.queries" in names
    assert "research.claims" in names
    # Every entry carries the version too — a name alone is unreproducible the first time a
    # prompt is revised, which is the `(family_id, version)` rule one layer up.
    assert all(entry["version"] and entry["calls"] >= 1 for entry in prompts)
    # First-call order, which is stage order: the brief cannot follow the write.
    assert names.index("editorial.brief") < names.index("research.queries")


def test_a_mode_name_this_version_cannot_read_is_refused_by_name(session, spawned):
    """422 rather than 409: the field's value is wrong, not the request's timing."""
    hook, structure, visual = templates(session)
    try:
        response = _client(session).post(
            "/drafts/workflow",
            json={
                "idea": "why clearer product writing matters",
                "hook_id": hook.id,
                "structure_id": structure.id,
                "visual_id": visual.id,
                "research_mode": "exhaustive",
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422, response.text
    assert "exhaustive" in response.json()["detail"]
    assert spawned == []


def test_a_below_floor_batch_writes_no_variants_at_all(session):
    """One press of Write variants would otherwise leave `variants_max` identical failures."""
    templates(session)
    try:
        response = _client(session).post(
            "/drafts/variants",
            json={"idea": "What changed in Acme checkout?", "research_mode": "none"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 409, response.text
    assert response.json()["detail"]["recommended_mode"] == "light"
    assert session.exec(select(Draft)).all() == []


def test_a_below_floor_retopic_is_refused_before_the_source_is_resolved(session):
    """The depth is checked against the **new** subject, which is the only one being written."""
    hook, structure, visual = templates(session)
    source = Draft(
        idea="an opinion piece",
        generation_stage="ready",
        hook_family=hook.family_id,
        hook_version=hook.version,
        structure_family=structure.family_id,
        structure_version=structure.version,
        visual_family=visual.family_id,
        visual_version=visual.version,
    )
    session.add(source)
    session.commit()
    try:
        response = _client(session).post(
            "/drafts/retopic",
            json={
                "idea": "What changed in Acme checkout?",
                "source_draft_id": source.id,
                "research_mode": "none",
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 409, response.text
    assert response.json()["detail"]["requested_mode"] == "none"
    # The source is untouched and no second row was written.
    assert [d.id for d in session.exec(select(Draft)).all()] == [source.id]


def test_invalid_write_schema_is_a_recoverable_failed_draft(session):
    draft = run(
        session,
        QueuedLLM(BRIEF, ANGLE_NONE, {"hook": "missing body"}),
        idea="why clearer product writing matters",
        angle=ANGLE_NONE,
    )

    assert draft.generation_stage == "failed"
    assert "missing required field 'body'" in (draft.generation_error or "")


def test_uncited_research_claim_is_not_revised_into_plausible_wording(session):
    llm = QueuedLLM(
        BRIEF,
        ANGLE_FACT,
        {"queries": ["Acme checkout"]},
        {"claims": [{"text": ANGLE_FACT["claims"][0]["text"], "citations": []}], "unknowns": []},
        WRITE_FACT,
    )
    draft = run(
        session,
        llm,
        idea="What changed in Acme checkout?",
        angle=ANGLE_FACT,
        requested_mode=LIGHT,
    )

    assert draft.generation_stage == "failed_review"
    assert any(item["gate"] == "uncited_claim" for item in draft.gate_results)
    assert len(llm.calls) == 5


def test_contradicted_claim_is_a_blocking_finding(session):
    llm = QueuedLLM(
        BRIEF,
        ANGLE_FACT,
        {"queries": ["Acme checkout"]},
        {
            "claims": [
                {
                    "text": ANGLE_FACT["claims"][0]["text"],
                    "citations": [
                        {
                            "source": "S1",
                            "span": "Acme changed its checkout flow to remove one field",
                            "stance": "contradicts",
                        }
                    ],
                }
            ],
            "unknowns": [],
        },
        WRITE_FACT,
    )
    draft = run(
        session,
        llm,
        idea="What changed in Acme checkout?",
        angle=ANGLE_FACT,
        requested_mode=LIGHT,
    )

    assert draft.generation_stage == "failed_review"
    assert any(item["gate"] == "contradicted_claim" for item in draft.gate_results)
    assert len(llm.calls) == 5


def test_revision_is_bounded_and_keeps_a_failed_review_state(session):
    short = {"hook": "Too short", "body": "Still short", "visual_values": {}}
    llm = QueuedLLM(BRIEF, ANGLE_NONE, short, {"body": "Still short"})
    draft = run(session, llm, idea="why clearer product writing matters", angle=ANGLE_NONE)

    assert draft.generation_stage == "failed_review"
    assert draft.revision_rounds <= 2
    assert len(llm.calls) == 4


def test_visual_failure_preserves_reviewed_written_content(session):
    draft = run(
        session,
        QueuedLLM(BRIEF, ANGLE_NONE, WRITE_NONE, VERIFIED, READY),
        idea="why clearer product writing matters",
        angle=ANGLE_NONE,
        renderer=Renderer(RuntimeError("render unavailable")),
    )

    assert draft.generation_stage == "ready"
    assert draft.full_text.startswith("Clarity is often subtraction")
    assert draft.visual_image is None
    assert "render unavailable" in (draft.visual_error or "")


def test_workflow_api_returns_persisted_lineage_and_blocks_failed_push(session, spawned):
    """The route hands back a `planning` draft; the run it started fills the lineage in.

    Two halves that used to be one: the request no longer waits for the pipeline, so the
    lineage assertions read the row *after* the run rather than out of the 201. The run is
    driven here by calling `generate_reviewed_draft` the way the worker does, because what
    this test is about is the persisted result and the push boundary — `tests/test_workflow.py`
    is where the worker's own commits are exercised.
    """
    hook, structure, visual = templates(session)
    llm = QueuedLLM(BRIEF, ANGLE_NONE, WRITE_NONE, VERIFIED, READY)
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_llm] = lambda: llm
    app.dependency_overrides[get_search] = Search
    app.dependency_overrides[get_html_renderer] = lambda: Renderer()
    try:
        client = TestClient(app)
        response = client.post(
            "/drafts/workflow",
            json={
                "idea": "why clearer product writing matters",
                "hook_id": hook.id,
                "structure_id": structure.id,
                "visual_id": visual.id,
            },
        )
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["generation_stage"] == "planning"
        assert body["editorial"] is None
        assert spawned == [(body["id"], None)]

        generate_reviewed_draft(
            session,
            llm,
            Search(),
            Renderer(),
            ImageRenderer(),
            idea="why clearer product writing matters",
            draft=session.get(Draft, body["id"]),
        )
        session.commit()

        read = client.get(f"/drafts/{body['id']}").json()
        assert read["generation_stage"] == "ready"
        assert read["editorial"]["brief"]["research_mode"] == "none"
        assert read["editorial"]["planned_claims"]

        stored = session.get(Draft, body["id"])
        stored.generation_stage = "failed_review"
        stored.generation_error = "test gate failure"
        session.commit()
        refused = client.post(f"/drafts/{body['id']}/push")
        assert refused.status_code == 409
        assert "not review-ready" in refused.json()["detail"]
    finally:
        app.dependency_overrides.clear()


def test_historical_draft_api_has_null_editorial_lineage(session):
    """A row from before the editorial migration still opens, and says it is `ready`.

    `generation_stage` is set explicitly here rather than left to default, and that is what
    makes this a test about a *historical* draft: the migration backfilled every existing row
    to `ready`, where the column's default for anything created since is `unreviewed`. A
    hand-built draft that took the default would be modelling a new legacy-path row instead,
    which is a different state and reads differently in Studio.
    """
    hook, structure, visual = templates(session)
    draft = Draft(
        idea="historical",
        generation_stage="ready",
        hook_family=hook.family_id,
        hook_version=hook.version,
        structure_family=structure.family_id,
        structure_version=structure.version,
        visual_family=visual.family_id,
        visual_version=visual.version,
    )
    session.add(draft)
    session.commit()
    session.refresh(draft)
    app.dependency_overrides[get_session] = lambda: session
    try:
        body = TestClient(app).get(f"/drafts/{draft.id}").json()
        assert body["generation_stage"] == "ready"
        assert body["editorial"] is None
    finally:
        app.dependency_overrides.clear()
