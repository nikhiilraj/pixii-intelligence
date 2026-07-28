import pytest

from app.generation import (
    NoUsableTemplates,
    generate_draft,
    regenerate_text,
    regenerate_visual,
    suggest_templates,
)
from app.models.post import Post
from app.models.template import TemplateKind
from app.templates import approve, create_template


class FakeLLM:
    def __init__(self, *responses: dict):
        self.responses = list(responses)
        self.calls: list[tuple[str, str]] = []

    def complete_json(self, system: str, user: str) -> dict:
        self.calls.append((system, user))
        return self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]

    @property
    def last_user(self) -> str:
        return self.calls[-1][1]


class FakeRenderer:
    def __init__(self, image: bytes = b"IMAGE"):
        self.image = image
        self.calls = 0

    def screenshot(self, html: str, width: int, height: int) -> bytes:
        self.calls += 1
        return self.image


WRITTEN = {
    "hook": "$450 turned into $19k/mo in recurring revenue.",
    "body": "not a rebrand. not a new ad budget. one Amazon main image.",
    "visual_values": {"big_number": "$19k/mo", "headline": "One image. Every month."},
}

CHOSEN = {"hook": "transformation", "structure": "case-study-loop", "visual": "stat-hero"}


def library(session, *, approve_all: bool = True):
    hook = create_template(
        session,
        kind=TemplateKind.HOOK,
        name="transformation",
        body={"pattern": "{small} turned into {large}", "tone": "plain, lowercase"},
        provenance=["win-1"],
    )
    structure = create_template(
        session,
        kind=TemplateKind.STRUCTURE,
        name="case-study-loop",
        body={
            "post_type": "deep-research",
            "sections": [{"name": "result", "guidance": "Lead with the dollar outcome."}],
        },
    )
    visual = create_template(
        session,
        kind=TemplateKind.VISUAL,
        name="stat-hero",
        body={"renderer": "html", "html": "<b>{big_number}</b><p>{headline}</p>"},
        slots=[{"name": "big_number"}, {"name": "headline"}],
    )
    if approve_all:
        for template in (hook, structure, visual):
            approve(session, template)
    return hook, structure, visual


def add_post(session, zid: str, engaged: int, content: str) -> Post:
    post = Post(zernio_id=zid, platform="linkedin", content=content, engaged_actions=engaged)
    session.add(post)
    session.flush()
    return post


IDEA = "a seller tested one main image and got +17% CTR"


def test_an_idea_produces_a_draft_with_hook_body_and_visual(session):
    library(session)
    draft = generate_draft(session, FakeLLM(WRITTEN), FakeRenderer(), idea=IDEA)

    assert draft.hook_text.startswith("$450 turned into")
    assert "not a rebrand" in draft.body_text
    assert draft.visual_image is not None


def test_the_draft_records_the_family_and_version_of_all_three_templates(session):
    hook, structure, visual = library(session)

    draft = generate_draft(session, FakeLLM(WRITTEN), FakeRenderer(), idea=IDEA)

    assert (draft.hook_family, draft.hook_version) == (hook.family_id, hook.version)
    assert (draft.structure_family, draft.structure_version) == (
        structure.family_id,
        structure.version,
    )
    assert (draft.visual_family, draft.visual_version) == (visual.family_id, visual.version)


def test_lineage_survives_a_later_edit_of_the_template(session):
    """Attribution keys on family and version, so revising a template cannot rewrite history."""
    from app.templates import edit_template

    hook, _, _ = library(session)
    draft = generate_draft(session, FakeLLM(WRITTEN), FakeRenderer(), idea=IDEA)

    edit_template(session, hook, name="transformation-v2")

    assert draft.hook_version == 1
    assert draft.hook_family == hook.family_id


def test_a_directed_draft_is_marked_as_directed(session):
    library(session)

    draft = generate_draft(session, FakeLLM(WRITTEN), FakeRenderer(), idea=IDEA)

    assert draft.mode == "directed"
    assert draft.idea == IDEA


def test_generation_is_grounded_in_the_posts_the_hook_came_from(session):
    library(session)
    add_post(session, "win-1", 185, "The exemplar post body that proves the pattern.")

    llm = FakeLLM(WRITTEN)
    generate_draft(session, llm, FakeRenderer(), idea=IDEA)

    assert "The exemplar post body that proves the pattern." in llm.last_user


def test_the_chosen_templates_shape_the_prompt(session):
    library(session)

    llm = FakeLLM(WRITTEN)
    generate_draft(session, llm, FakeRenderer(), idea=IDEA)

    assert "{small} turned into {large}" in llm.last_user
    assert "Lead with the dollar outcome." in llm.last_user
    assert IDEA in llm.last_user


def test_an_explicit_choice_overrides_the_suggestion(session):
    library(session)
    other = create_template(
        session,
        kind=TemplateKind.HOOK,
        name="deadline",
        body={"pattern": "On {date}, {platform} will {change}"},
    )
    approve(session, other)

    draft = generate_draft(
        session, FakeLLM(WRITTEN), FakeRenderer(), idea=IDEA, hook_id=other.id
    )

    assert draft.hook_family == other.family_id


def test_generation_refuses_when_nothing_is_approved(session):
    library(session, approve_all=False)

    with pytest.raises(NoUsableTemplates):
        generate_draft(session, FakeLLM(WRITTEN), FakeRenderer(), idea=IDEA)


def test_suggestion_picks_from_the_approved_library_and_explains_itself(session):
    hook, structure, visual = library(session)

    suggestion = suggest_templates(session, FakeLLM({**CHOSEN, "reason": "case shaped"}), IDEA)

    assert suggestion.hook.id == hook.id
    assert suggestion.structure.id == structure.id
    assert suggestion.visual.id == visual.id
    assert suggestion.reason == "case shaped"


def test_a_suggestion_naming_something_unapproved_falls_back_rather_than_failing(session):
    hook, _, _ = library(session)

    suggestion = suggest_templates(session, FakeLLM({"hook": "invented", "structure": "x"}), IDEA)

    assert suggestion.hook.id == hook.id


def test_regenerating_text_replaces_the_words_and_keeps_the_lineage(session):
    library(session)
    draft = generate_draft(session, FakeLLM(WRITTEN), FakeRenderer(), idea=IDEA)
    original_family = draft.hook_family

    rewritten = {**WRITTEN, "hook": "A different opening entirely."}
    regenerate_text(session, FakeLLM(rewritten), draft)

    assert draft.hook_text == "A different opening entirely."
    assert draft.hook_family == original_family


def test_regenerating_the_visual_leaves_the_text_alone(session):
    library(session)
    renderer = FakeRenderer()
    draft = generate_draft(session, FakeLLM(WRITTEN), renderer, idea=IDEA)
    text_before = draft.body_text

    regenerate_visual(session, draft, FakeRenderer(image=b"NEWIMAGE"))

    assert draft.body_text == text_before
    assert draft.visual_image == b"NEWIMAGE"


def test_a_visual_missing_a_slot_value_does_not_lose_the_written_text(session):
    """The words are the expensive part — a failed image must not discard them."""
    library(session)
    incomplete = {**WRITTEN, "visual_values": {"big_number": "$19k"}}

    draft = generate_draft(session, FakeLLM(incomplete), FakeRenderer(), idea=IDEA)

    assert draft.body_text.startswith("not a rebrand")
    assert draft.visual_image is None
    assert draft.visual_error is not None


def image_slot_library(session):
    """A visual whose slots are not all text — the case that silently produced empty boxes."""
    hook = create_template(
        session, kind=TemplateKind.HOOK, name="transformation", body={"pattern": "{a}"}
    )
    structure = create_template(
        session, kind=TemplateKind.STRUCTURE, name="s", body={"sections": []}
    )
    visual = create_template(
        session,
        kind=TemplateKind.VISUAL,
        name="stat-hero",
        body={"renderer": "html", "html": "<b>{big_number}</b><img src='{left_image_url}'>"},
        slots=[
            {"name": "big_number", "type": "text"},
            {"name": "left_image_url", "type": "image_url"},
        ],
    )
    for t in (hook, structure, visual):
        approve(session, t)
    return hook, structure, visual


def test_the_model_is_only_asked_to_fill_text_slots(session):
    image_slot_library(session)

    llm = FakeLLM(WRITTEN)
    generate_draft(session, llm, FakeRenderer(), idea=IDEA)

    write_prompt = llm.calls[-1][1]
    assert "big_number" in write_prompt
    assert "left_image_url" not in write_prompt


def test_prose_written_into_an_image_slot_is_discarded(session):
    """A sentence in an <img src> renders as an empty box and reports success. Never allow it."""
    image_slot_library(session)
    invented = {**WRITTEN, "visual_values": {"big_number": "40", "left_image_url": "image CTR"}}

    draft = generate_draft(session, FakeLLM(invented), FakeRenderer(), idea=IDEA)

    assert "left_image_url" not in draft.visual_values


def test_an_unfilled_image_slot_reports_rather_than_rendering_an_empty_box(session):
    image_slot_library(session)

    draft = generate_draft(session, FakeLLM(WRITTEN), FakeRenderer(), idea=IDEA)

    assert draft.visual_image is None
    assert "left_image_url" in (draft.visual_error or "")
    assert draft.body_text.startswith("not a rebrand")
