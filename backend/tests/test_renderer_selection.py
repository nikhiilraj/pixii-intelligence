"""Which renderer draws a generated visual — and, the whole point, *when* that is decided.

The renderer used to be chosen at the route, from `payload.visual_id`, before generation had
suggested anything. A request naming no visual resolved `None` to the HTML renderer and then
let `suggest_templates` pick an `ai` template, so every model-suggested AI visual was handed a
renderer with no `generate`; `_draw_visual` filed the `UnsupportedRenderer` under
`visual_error` and the draft arrived looking generated with no picture. The autonomous run had
the same defect permanently, being injected an HTML renderer and never asking for another.

Every test here asks one question from a different entry point: does the renderer come from
the template row that was actually used?
"""

import pytest
from sqlmodel import select

from app.autonomous import run_autonomous
from app.generation import generate_draft, generate_reviewed_draft, retopic
from app.models.draft import Draft
from app.models.post import Post
from app.models.template import TemplateKind
from app.templates import approve, create_template, edit_template
from tests.test_generation import WorkflowLLM

# An opinion, deliberately. `retopic` and `run_autonomous` run the reviewed workflow now, and
# `resolve_mode` reads a named subject plus a number as a factual floor — so the previous
# subject ("one main image lifted CTR by 17%") would stop every test below at a failed
# research state, where no renderer is reached at all and the question this file asks cannot
# be answered. Which renderer draws a visual is independent of how deeply it was researched.
IDEA = "why a plain layout beats a busy one"

# Slotless on both sides, deliberately. `render_visual` fills the prompt skeleton *before*
# calling `generate`, so an unfilled `{slot}` raises `MissingSlotValue` and the image renderer
# is never reached — a test asserting "the image renderer drew it" would then fail for a
# reason that has nothing to do with which renderer was selected, before and after the fix.
HTML_BODY = {"renderer": "html", "html": "<p>Pixii</p>"}
AI_BODY = {"renderer": "ai", "prompt": "a plain cream field", "style_reference": "flat"}


class HtmlOnly:
    """Carries `screenshot` and nothing else, like the real Cloudflare renderer.

    Not one fake answering to both methods: `rendering.render_visual` dispatches on
    `hasattr(renderer, "generate")`, so a dual-method stub would draw an `ai` template through
    the HTML renderer without complaint and pass the one test that has to fail.
    """

    def __init__(self) -> None:
        self.calls = 0

    def screenshot(self, html: str, width: int, height: int) -> bytes:
        self.calls += 1
        return b"HTML"


class ImageOnly:
    """Carries `generate` and nothing else, like the real Azure renderer."""

    def __init__(self) -> None:
        self.calls = 0

    def generate(self, prompt: str, width: int, height: int) -> bytes:
        self.calls += 1
        return b"AI"


class BrokenImage(ImageOnly):
    def generate(self, prompt: str, width: int, height: int) -> bytes:
        self.calls += 1
        raise RuntimeError("image service declined")


# Long enough to clear `gates.POST_MIN_CHARS`. The reviewed workflow runs the deterministic
# gates over what the model returned, so a two-word body stops at `failed_review` and the run
# never reaches a renderer — which would fail every case in this file for a reason that has
# nothing to do with renderer selection. The gate is right and the fixture was written before
# any caller here was on the reviewed path.
WRITTEN = {
    "hook": "One image changed the month.",
    "body": (
        "Not a rebrand and not a bigger ad budget. One main image, redrawn once, and the "
        "listing did the rest. The lesson is that the cheapest change is usually the one "
        "nobody has looked at recently."
    ),
    "visual_values": {},
}
SUGGESTED = {"hook": "h", "structure": "s", "visual": "v", "reason": "fits the idea"}


class FakeLLM:
    """Answers the suggestion and write prompts, plus the reviewed workflow's own calls.

    The planning and review answers are borrowed from `tests/test_generation.WorkflowLLM`
    rather than restated, so there is one description of what a passing workflow says. Without
    them `retopic` and `run_autonomous` — both on the reviewed path now — would fail at the
    brief and never reach a renderer, which is the one thing this file is about.
    """

    def complete_json(self, system: str, user: str, images=()) -> dict:
        for marker, answer in WorkflowLLM.PLANNING.items():
            if system.lower().startswith(marker):
                return dict(answer)
        if "choose which templates" in system.lower():
            return dict(SUGGESTED)
        return dict(WRITTEN)


class NoSearch:
    """`IDEA` resolves to `none`, which never reaches a provider. A call here is a finding."""

    def search(self, query: str, *, limit: int):
        raise AssertionError(f"a renderer-selection test reached a web search: {query!r}")


class TopicLLM(FakeLLM):
    """The same fake with a topic proposal in front, which is where a run starts."""

    def complete_json(self, system: str, user: str, images=()) -> dict:
        if "topic" in system.lower():
            return {"topics": [{"idea": IDEA}]}
        return super().complete_json(system, user, images)


def library(session, *, visual_body: dict):
    hook = create_template(session, kind=TemplateKind.HOOK, name="h", body={"pattern": "{a}"})
    structure = create_template(
        session, kind=TemplateKind.STRUCTURE, name="s", body={"sections": []}
    )
    visual = create_template(
        session, kind=TemplateKind.VISUAL, name="v", body=visual_body, slots=[]
    )
    for template in (hook, structure, visual):
        approve(session, template)
    return hook, structure, visual


# --- generate_draft: the template chosen by the caller ---------------------------------------


def test_a_chosen_html_visual_is_drawn_by_the_html_renderer(session):
    _, _, visual = library(session, visual_body=HTML_BODY)
    html, image = HtmlOnly(), ImageOnly()

    draft = generate_draft(session, FakeLLM(), html, image, idea=IDEA, visual_id=visual.id)

    assert (html.calls, image.calls) == (1, 0)
    assert draft.visual_image == b"HTML"
    assert draft.visual_error is None


def test_a_chosen_ai_visual_is_drawn_by_the_image_renderer(session):
    _, _, visual = library(session, visual_body=AI_BODY)
    html, image = HtmlOnly(), ImageOnly()

    draft = generate_draft(session, FakeLLM(), html, image, idea=IDEA, visual_id=visual.id)

    assert (html.calls, image.calls) == (0, 1)
    assert draft.visual_image == b"AI"
    assert draft.visual_error is None


# --- generate_draft: the template the model chose --------------------------------------------


def test_a_suggested_html_visual_is_drawn_by_the_html_renderer(session):
    library(session, visual_body=HTML_BODY)
    html, image = HtmlOnly(), ImageOnly()

    draft = generate_draft(session, FakeLLM(), html, image, idea=IDEA)

    assert (html.calls, image.calls) == (1, 0)
    assert draft.visual_image == b"HTML"


def test_a_suggested_ai_visual_is_drawn_by_the_image_renderer(session):
    """The regression. No `visual_id` reaches the route, so nothing knew this was an AI
    template until `suggest_templates` had run — and the renderer had already been picked."""
    library(session, visual_body=AI_BODY)
    html, image = HtmlOnly(), ImageOnly()

    draft = generate_draft(session, FakeLLM(), html, image, idea=IDEA)

    assert (html.calls, image.calls) == (0, 1)
    assert draft.visual_image == b"AI"
    assert draft.visual_error is None


# --- lineage: the row the renderer was read off is the row the draft records -----------------


def test_the_renderer_comes_from_the_recorded_version_not_the_newest_in_the_family(session):
    """v1 declares `ai`, v2 declares `html`, and the caller chose v1.

    `edit_template` carries APPROVED forward, so v2 is both newer and usable — which is
    exactly the arrangement in which "resolve the renderer from the family" and "resolve it
    from the version this draft names" give different answers. The draft's own
    `(visual_family, visual_version)` has to be the row the renderer was read off.
    """
    _, _, visual = library(session, visual_body=AI_BODY)
    edit_template(session, visual, body=dict(HTML_BODY))
    html, image = HtmlOnly(), ImageOnly()

    draft = generate_draft(session, FakeLLM(), html, image, idea=IDEA, visual_id=visual.id)

    assert (html.calls, image.calls) == (0, 1)
    assert (draft.visual_family, draft.visual_version) == (visual.family_id, 1)


def test_a_retopic_draws_through_the_inherited_version_not_the_newest(session):
    """The two rules composed, which is where a regression would hide.

    A re-topic inherits the source's exact `(family, version)` and must be *drawn* by that
    row too. Its renderer used to come from `api_drafts._renderer`, a call site this change
    removed, so nothing else would notice it going wrong: with an `html` v2 on top of an `ai`
    v1, resolving either the templates or the renderer by newest silently swaps the picture.
    """
    _, _, visual = library(session, visual_body=AI_BODY)
    source = generate_draft(
        session, FakeLLM(), HtmlOnly(), ImageOnly(), idea=IDEA, visual_id=visual.id
    )
    edit_template(session, visual, body=dict(HTML_BODY))
    html, image = HtmlOnly(), ImageOnly()

    fresh = retopic(
        session, FakeLLM(), NoSearch(), html, image, source, idea="a different subject"
    )

    assert fresh.id != source.id
    assert (html.calls, image.calls) == (0, 1)
    assert (fresh.visual_family, fresh.visual_version) == (visual.family_id, 1)
    assert fresh.visual_image == b"AI"


# --- a renderer that fails must not cost the words -------------------------------------------


def test_a_failed_image_render_keeps_the_words_and_records_why(session):
    library(session, visual_body=AI_BODY)
    html, image = HtmlOnly(), BrokenImage()

    draft = generate_draft(session, FakeLLM(), html, image, idea=IDEA)

    assert image.calls == 1 and html.calls == 0
    assert draft.visual_image is None
    assert "image service declined" in (draft.visual_error or "")
    assert draft.hook_text == WRITTEN["hook"]
    assert draft.body_text == WRITTEN["body"]


# --- the reviewed Studio pipeline ------------------------------------------------------------

BRIEF = {
    "objective": "Help operators explain one useful product decision.",
    "audience": "product operators responsible for checkout",
    "desired_action": "review one unnecessary checkout step",
    "constraints": [],
}
ANGLE = {
    "thesis": "clearer product writing improves internal decisions",
    "tension": "teams confuse more detail with more clarity",
    "audience_stake": "operators spend less time resolving avoidable ambiguity",
    "claims": [{"text": "clearer product writing helps teams make decisions"}],
    "beats": ["name the ambiguity", "show the editing principle"],
    "cta": "remove one sentence that does not change the decision",
}
REVIEWED_WRITE = {
    "hook": "Clarity is often subtraction.",
    "body": (
        "Most teams add detail when a decision feels unclear. The better move is often "
        "deletion. Remove the sentence that changes no choice, then ask whether the next "
        "action is obvious."
    ),
    "visual_values": {},
}
READY = {"deductions": []}
REVIEWED_IDEA = "why clearer product writing matters"


class QueuedLLM:
    """Answers in order and refuses a call it has no answer for, so an extra model call is a
    failure rather than a repeat of the last response."""

    def __init__(self, *answers: dict) -> None:
        self.answers = list(answers)

    def complete_json(self, system: str, user: str, images=()) -> dict:
        if not self.answers:
            raise AssertionError("workflow made an unbounded model call")
        return self.answers.pop(0)


class Search:
    def search(self, query: str, *, limit: int):  # pragma: no cover — no research runs here
        return []


def reviewed(session, html, image, *, chosen=None):
    """`chosen` is the whole (hook, structure, visual) triple or nothing at all.

    Not the visual alone: `generate_reviewed_draft` suggests when *any* of the three is
    absent, so pinning one and leaving two would still run a suggestion — and the chosen and
    suggested cases below would stop being different tests.
    """
    answers = [BRIEF, ANGLE, REVIEWED_WRITE, READY]
    if chosen is None:
        answers.insert(0, SUGGESTED)
    hook, structure, visual = chosen or (None, None, None)
    return generate_reviewed_draft(
        session,
        QueuedLLM(*answers),
        Search(),
        html,
        image,
        idea=REVIEWED_IDEA,
        hook_id=hook.id if hook else None,
        structure_id=structure.id if structure else None,
        visual_id=visual.id if visual else None,
    )


@pytest.mark.parametrize(
    ("body", "drawn"), [(HTML_BODY, (1, 0)), (AI_BODY, (0, 1))], ids=["html", "ai"]
)
def test_the_reviewed_workflow_draws_a_chosen_visual_with_its_own_renderer(session, body, drawn):
    html, image = HtmlOnly(), ImageOnly()

    reviewed(session, html, image, chosen=library(session, visual_body=body))

    assert (html.calls, image.calls) == drawn


@pytest.mark.parametrize(
    ("body", "drawn"), [(HTML_BODY, (1, 0)), (AI_BODY, (0, 1))], ids=["html", "ai"]
)
def test_the_reviewed_workflow_draws_a_suggested_visual_with_its_own_renderer(session, body, drawn):
    """The `ai` half is the regression: `POST /drafts/workflow` picked the renderer from an
    absent `payload.visual_id` and only then let the model choose an AI template."""
    library(session, visual_body=body)
    html, image = HtmlOnly(), ImageOnly()

    reviewed(session, html, image)

    assert (html.calls, image.calls) == drawn


def test_a_failed_render_leaves_the_reviewed_words_and_the_readiness_decision_alone(session):
    """`_draw_visual` runs after readiness has already been decided. A picture that did not
    come out must cost neither the reviewed words nor the stage that judged them."""
    library(session, visual_body=AI_BODY)
    html, image = HtmlOnly(), BrokenImage()

    draft = reviewed(session, html, image)

    assert image.calls == 1
    assert draft.visual_image is None
    assert "image service declined" in (draft.visual_error or "")
    assert draft.hook_text == REVIEWED_WRITE["hook"]
    assert draft.body_text == REVIEWED_WRITE["body"]
    assert draft.generation_stage == "ready"


# --- the unattended run ----------------------------------------------------------------------


def test_an_autonomous_run_draws_a_suggested_ai_visual_with_the_image_renderer(session):
    """`run_autonomous` names no templates at all, so every visual it draws is suggested. It
    was injected an HTML renderer and nothing else, which made this failure permanent."""
    library(session, visual_body=AI_BODY)
    session.add(Post(zernio_id="p1", platform="linkedin", content="post p1", engaged_actions=10))
    session.flush()
    html, image = HtmlOnly(), ImageOnly()

    result = run_autonomous(
        session, TopicLLM(), NoSearch(), html, image, cap=1, notify=lambda _: None
    )

    assert result.created == 1
    assert result.visuals_failed == 0
    assert (html.calls, image.calls) == (0, 1)
    draft = session.exec(select(Draft)).one()
    assert draft.visual_image == b"AI"
    assert draft.visual_error is None
