"""The connected Studio path, from editorial artifacts through review and rendering."""

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

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
        idea=idea,
        hook_id=hook.id,
        structure_id=structure.id,
        visual_id=visual.id,
        requested_mode=requested_mode,
        research_fetcher=fetched,
    )


def test_no_research_still_plans_and_reaches_ready(session):
    llm = QueuedLLM(BRIEF, ANGLE_NONE, WRITE_NONE, READY)
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
    assert "Sources:" in llm.calls[-2][1]


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
        QueuedLLM(BRIEF, ANGLE_NONE, WRITE_NONE, READY),
        idea="why clearer product writing matters",
        angle=ANGLE_NONE,
        renderer=Renderer(RuntimeError("render unavailable")),
    )

    assert draft.generation_stage == "ready"
    assert draft.full_text.startswith("Clarity is often subtraction")
    assert draft.visual_image is None
    assert "render unavailable" in (draft.visual_error or "")


def test_workflow_api_returns_persisted_lineage_and_blocks_failed_push(session):
    hook, structure, visual = templates(session)
    llm = QueuedLLM(BRIEF, ANGLE_NONE, WRITE_NONE, READY)
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
        assert body["generation_stage"] == "ready"
        assert body["editorial"]["brief"]["research_mode"] == "none"
        assert body["editorial"]["planned_claims"]

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
    hook, structure, visual = templates(session)
    draft = Draft(
        idea="historical",
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
