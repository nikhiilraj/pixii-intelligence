"""Nothing a human can act on may exist without having crossed the review boundary.

Every route that creates a draft is here, against one question: can what it produced be
pushed? The bug this file was opened for is that `Draft.generation_stage` defaulted to
`ready`, so four separate creation paths — legacy `POST /drafts`, variants, retopic and the
autonomous run — minted drafts a human could push with no brief, no research, no gates and
no rubric behind them. The push guard was reading a column nothing had set.
"""

from fastapi.testclient import TestClient

from app.db import get_session
from app.deps import get_html_renderer, get_image_renderer, get_llm
from app.main import app
from app.models.draft import Draft
from app.models.stage import GenerationStage
from app.models.template import TemplateKind
from app.templates import approve, create_template

WRITTEN = {"hook": "A hook line.", "body": "The body of the post.", "visual_values": {}}


class StubLLM:
    """Answers whatever is asked with the same written post. Never runs out."""

    def complete_json(self, system: str, user: str, images=()) -> dict:
        return dict(WRITTEN)


class Renderer:
    def screenshot(self, html: str, width: int, height: int) -> bytes:
        return b"PNG"


def approved_templates(session):
    hook = create_template(
        session, kind=TemplateKind.HOOK, name="plain", body={"pattern": "p", "tone": "t"}
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


def api(session, llm=None):
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_llm] = lambda: llm or StubLLM()
    app.dependency_overrides[get_html_renderer] = Renderer
    app.dependency_overrides[get_image_renderer] = Renderer
    return TestClient(app)


def test_a_draft_nobody_set_a_stage_on_is_unreviewed(session):
    """The default, read off a row. Not `ready`, which is what it used to be."""
    hook, structure, visual = approved_templates(session)
    draft = Draft(
        idea="an idea",
        hook_family=hook.family_id,
        hook_version=hook.version,
        structure_family=structure.family_id,
        structure_version=structure.version,
        visual_family=visual.family_id,
        visual_version=visual.version,
    )
    session.add(draft)
    session.flush()
    assert draft.generation_stage == GenerationStage.UNREVIEWED


def test_the_legacy_create_route_produces_a_draft_that_cannot_be_pushed(session):
    """`POST /drafts` writes words and reviews nothing. It is deprecated, and it is refused.

    It stays reachable for existing API clients rather than being deleted, so this is the
    test that keeps it from being a bypass: whatever it produces stops at the boundary.
    """
    hook, structure, visual = approved_templates(session)
    try:
        client = api(session)
        created = client.post(
            "/drafts",
            json={
                "idea": "an idea",
                "hook_id": hook.id,
                "structure_id": structure.id,
                "visual_id": visual.id,
            },
        )
        assert created.status_code == 201, created.text
        assert created.json()["generation_stage"] == GenerationStage.UNREVIEWED

        refused = client.post(f"/drafts/{created.json()['id']}/push")
        assert refused.status_code == 409
        assert "not review-ready" in refused.json()["detail"]
    finally:
        app.dependency_overrides.clear()
