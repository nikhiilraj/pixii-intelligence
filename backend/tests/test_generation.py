from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, col, select

from app.api_drafts import DraftOut, _renderer
from app.config import settings
from app.db import get_session
from app.deps import get_html_renderer, get_llm, get_search, get_zernio
from app.generation import (
    LESSON_LIMIT,
    NoUsableTemplates,
    generate_draft,
    generated_from,
    regenerate_text,
    regenerate_visual,
    retopic,
    suggest_templates,
    variant_combinations,
    writable_slots,
)
from app.main import app
from app.models.draft import Draft
from app.models.post import Post, Verdict
from app.models.stage import GenerationStage
from app.models.template import TemplateKind, TemplateStatus
from app.publishing import PushFailed
from app.templates import approve, create_template, edit_template, retire
from tests.test_rendering import renderer_capturing


@pytest.fixture
def client(session: Session) -> Iterator[TestClient]:
    app.dependency_overrides[get_session] = lambda: session
    yield TestClient(app)
    app.dependency_overrides.clear()


class FakeLLM:
    def __init__(self, *responses: dict):
        self.responses = list(responses)
        self.calls: list[tuple[str, str]] = []

    def complete_json(self, system: str, user: str, images=()) -> dict:
        self.calls.append((system, user))
        return self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]

    @property
    def last_user(self) -> str:
        return self.calls[-1][1]


class WorkflowLLM(FakeLLM):
    """`FakeLLM` that also answers the reviewed workflow's planning and review calls.

    `retopic` and `POST /drafts/variants` run `generate_reviewed_draft` now, so a stub that
    answered every call with the written post would hand a *post* to the brief step. The
    workflow records that as a planning failure and returns rather than raising, so every
    assertion about lineage below would quietly become an assertion about a `failed` row —
    green where it mattered, measuring nothing.

    The write answers still come from `FakeLLM`'s ordered queue, so a test supplying two posts
    still gets two. The planning and review answers are constant and are deliberately not
    drawn from that queue: a brief consuming a queued *post* is the bug this exists to avoid.
    """

    # Matched on the opening of each system prompt rather than on call order — see
    # `tests/test_autonomous._ANSWERS` for why order is the wrong key here.
    PLANNING = {
        "you turn a post idea into an editorial brief": {
            "objective": "Help operators explain one product decision.",
            "audience": "operators responsible for the funnel",
            "desired_action": "review one unnecessary step",
            "constraints": [],
        },
        "you plan the argument of one post": {
            "thesis": "clearer product writing improves internal decisions",
            "tension": "teams confuse more detail with more clarity",
            "audience_stake": "operators spend less time resolving ambiguity",
            "claims": [{"text": "clearer product writing helps teams decide"}],
            "beats": ["name the ambiguity", "show the editing principle"],
            "cta": "remove one sentence that changes no decision",
        },
        # No deductions: ready. A test wanting a failed review says so itself.
        "you review one draft linkedin post": {"deductions": []},
    }

    def complete_json(self, system: str, user: str, images=()) -> dict:
        for marker, answer in self.PLANNING.items():
            if system.lower().startswith(marker):
                self.calls.append((system, user))
                return answer
        return super().complete_json(system, user, images)

    @property
    def last_write(self) -> str:
        """The most recent *write* prompt, which is what the lineage assertions are about.

        `last_user` is the last call of any kind, and the workflow's last call is the rubric.
        Asserting the templates reached the model has to read the prompt that carried them.
        """
        for _system, user in reversed(self.calls):
            if "visual slots" in user.lower() or "approved hook template" in user.lower():
                return user
        raise AssertionError("no write prompt was sent")


# Completions one reviewed draft buys when its three templates are named: brief, angle, write,
# rubric. No suggest call — `retopic` and `/drafts/variants` both choose the templates
# themselves. Named rather than repeated, because it is a property of the workflow and moves
# with it.
WORKFLOW_CALLS = 4


class NoSearch:
    """A search adapter the reviewed workflow now carries. Nothing here should reach it.

    Every idea in this file is an opinion, so `resolve_mode` puts it at `none` and `none`
    never reaches a provider. A call arriving here means the research floor moved under a
    test that is about template lineage, which is worth failing on rather than absorbing.
    """

    def search(self, query: str, *, limit: int):
        raise AssertionError(f"a generation test reached a web search: {query!r}")


class FakeRenderer:
    def __init__(self, image: bytes = b"IMAGE"):
        self.image = image
        self.calls = 0

    def screenshot(self, html: str, width: int, height: int) -> bytes:
        self.calls += 1
        return self.image


class FakeImageRenderer:
    """The second renderer generation now takes, for the `ai` templates this file has none of.

    Returns bytes nothing here expects, deliberately: every visual below declares `html`, so a
    draft coming back with `GENERATED` means the renderer was chosen from something other than
    the template — which is the defect `generation._renderer_for` exists to prevent. It cannot
    also carry `screenshot`; `render_visual` dispatches on `hasattr`.
    """

    def generate(self, prompt: str, width: int, height: int) -> bytes:
        return b"GENERATED"


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
        slots=[
            # Typed, because untyped is not writable — see
            # `test_an_untyped_asset_slot_is_not_writable`.
            {"name": "big_number", "type": "text"},
            {"name": "headline", "type": "text"},
        ],
    )
    if approve_all:
        for template in (hook, structure, visual):
            approve(session, template)
    return hook, structure, visual


def add_post(session, zid: str, engaged: int, content: str, author: str | None = None) -> Post:
    post = Post(
        zernio_id=zid,
        platform="linkedin",
        content=content,
        engaged_actions=engaged,
        account_username=author or settings.voice_account,
    )
    session.add(post)
    session.flush()
    return post


IDEA = "a seller tested one main image and got +17% CTR"


def test_an_idea_produces_a_draft_with_hook_body_and_visual(session):
    library(session)
    draft = generate_draft(
        session, FakeLLM(WRITTEN), FakeRenderer(), FakeImageRenderer(), idea=IDEA
    )

    assert draft.hook_text.startswith("$450 turned into")
    assert "not a rebrand" in draft.body_text
    assert draft.visual_image is not None


def test_the_draft_records_the_family_and_version_of_all_three_templates(session):
    hook, structure, visual = library(session)

    draft = generate_draft(
        session, FakeLLM(WRITTEN), FakeRenderer(), FakeImageRenderer(), idea=IDEA
    )

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
    draft = generate_draft(
        session, FakeLLM(WRITTEN), FakeRenderer(), FakeImageRenderer(), idea=IDEA
    )

    edit_template(session, hook, name="transformation-v2")

    assert draft.hook_version == 1
    assert draft.hook_family == hook.family_id


def test_a_directed_draft_is_marked_as_directed(session):
    library(session)

    draft = generate_draft(
        session, FakeLLM(WRITTEN), FakeRenderer(), FakeImageRenderer(), idea=IDEA
    )

    assert draft.mode == "directed"
    assert draft.idea == IDEA


def test_generation_is_grounded_in_the_posts_the_hook_came_from(session):
    library(session)
    add_post(session, "win-1", 185, "The exemplar post body that proves the pattern.")

    llm = FakeLLM(WRITTEN)
    generate_draft(session, llm, FakeRenderer(), FakeImageRenderer(), idea=IDEA)

    assert "The exemplar post body that proves the pattern." in llm.last_user


def test_a_creator_post_is_never_handed_to_the_model_as_a_voice_to_imitate(session):
    """The hard line. Hooks may be borrowed from other creators; voice may not.

    The creator post is named in the hook's own provenance and outranks Monte's, so only
    the cohort restriction inside `_exemplars` keeps it out of the prompt. Widening that
    query — or trusting the cohort a hook claims — fails this test.
    """
    hook, _, _ = library(session)
    hook.provenance = ["win-1", "theirs"]
    session.add(hook)
    session.flush()
    add_post(session, "win-1", 185, "Monte's own post body.")
    add_post(
        session,
        "theirs",
        5000,
        "Someone else's writing.",
        author=settings.inspiration_account,
    )

    llm = FakeLLM(WRITTEN)
    generate_draft(session, llm, FakeRenderer(), FakeImageRenderer(), idea=IDEA)

    assert "Monte's own post body." in llm.last_user
    assert "Someone else's writing." not in llm.last_user


def test_a_regenerated_draft_is_held_to_the_same_voice_line(session):
    hook, _, _ = library(session)
    hook.provenance = ["theirs"]
    session.add(hook)
    session.flush()
    add_post(
        session,
        "theirs",
        5000,
        "Someone else's writing.",
        author=settings.inspiration_account,
    )

    llm = FakeLLM(WRITTEN)
    draft = generate_draft(session, llm, FakeRenderer(), FakeImageRenderer(), idea=IDEA)
    regenerate_text(session, llm, draft)

    assert "Someone else's writing." not in llm.last_user


def test_the_chosen_templates_shape_the_prompt(session):
    library(session)

    llm = FakeLLM(WRITTEN)
    generate_draft(session, llm, FakeRenderer(), FakeImageRenderer(), idea=IDEA)

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
        session, FakeLLM(WRITTEN), FakeRenderer(), FakeImageRenderer(), idea=IDEA, hook_id=other.id
    )

    assert draft.hook_family == other.family_id


def test_generation_refuses_when_nothing_is_approved(session):
    library(session, approve_all=False)

    with pytest.raises(NoUsableTemplates):
        generate_draft(session, FakeLLM(WRITTEN), FakeRenderer(), FakeImageRenderer(), idea=IDEA)


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
    draft = generate_draft(
        session, FakeLLM(WRITTEN), FakeRenderer(), FakeImageRenderer(), idea=IDEA
    )
    original_family = draft.hook_family

    rewritten = {**WRITTEN, "hook": "A different opening entirely."}
    regenerate_text(session, FakeLLM(rewritten), draft)

    assert draft.hook_text == "A different opening entirely."
    assert draft.hook_family == original_family


def test_regenerating_the_visual_leaves_the_text_alone(session):
    library(session)
    renderer = FakeRenderer()
    draft = generate_draft(session, FakeLLM(WRITTEN), renderer, FakeImageRenderer(), idea=IDEA)
    text_before = draft.body_text

    regenerate_visual(session, draft, FakeRenderer(image=b"NEWIMAGE"))

    assert draft.body_text == text_before
    assert draft.visual_image == b"NEWIMAGE"


def test_a_visual_missing_a_slot_value_does_not_lose_the_written_text(session):
    """The words are the expensive part — a failed image must not discard them."""
    library(session)
    incomplete = {**WRITTEN, "visual_values": {"big_number": "$19k"}}

    draft = generate_draft(
        session, FakeLLM(incomplete), FakeRenderer(), FakeImageRenderer(), idea=IDEA
    )

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
    generate_draft(session, llm, FakeRenderer(), FakeImageRenderer(), idea=IDEA)

    write_prompt = llm.calls[-1][1]
    assert "big_number" in write_prompt
    assert "left_image_url" not in write_prompt


def test_prose_written_into_an_image_slot_is_discarded(session):
    """A sentence in an <img src> renders as an empty box and reports success. Never allow it."""
    image_slot_library(session)
    invented = {**WRITTEN, "visual_values": {"big_number": "40", "left_image_url": "image CTR"}}

    draft = generate_draft(
        session, FakeLLM(invented), FakeRenderer(), FakeImageRenderer(), idea=IDEA
    )

    assert "left_image_url" not in draft.visual_values


def test_an_unfilled_image_slot_reports_rather_than_rendering_an_empty_box(session):
    image_slot_library(session)

    draft = generate_draft(
        session, FakeLLM(WRITTEN), FakeRenderer(), FakeImageRenderer(), idea=IDEA
    )

    assert draft.visual_image is None
    assert "left_image_url" in (draft.visual_error or "")
    assert draft.body_text.startswith("not a rebrand")


def untyped_slot_library(session):
    """The legacy shape: a visual authored before slots declared a `type`.

    `stat-hero` v1 was live in this state — APPROVED, four untyped slots, two of them an
    `<img src>`. Kept as a fixture because retiring that row does not delete the shape.
    """
    hook = create_template(
        session, kind=TemplateKind.HOOK, name="transformation", body={"pattern": "{a}"}
    )
    structure = create_template(
        session, kind=TemplateKind.STRUCTURE, name="s", body={"sections": []}
    )
    visual = create_template(
        session,
        kind=TemplateKind.VISUAL,
        name="stat-hero-v1",
        body={"renderer": "html", "html": "<b>{big_number}</b><img src='{left_image_url}'>"},
        slots=[{"name": "big_number"}, {"name": "left_image_url"}],
    )
    for t in (hook, structure, visual):
        approve(session, t)
    return hook, structure, visual


def test_an_untyped_asset_slot_is_not_writable(session):
    """An untyped slot is an unknown slot, and no name proves it is safe.

    `left_image_url` reads as an asset, but `subject`, `logo` and `hero` do not, so the
    guard cannot key on the name. Absent `type` means absent, and absent is not writable.
    """
    _, _, visual = untyped_slot_library(session)

    assert writable_slots(visual) == []


def test_an_untyped_template_fails_loudly_instead_of_rendering_empty_boxes(session):
    """The exact live failure, reproduced: the model volunteers prose for the `<img src>`.

    While `""` counted as writable, that prose was accepted, Cloudflare rendered the page,
    the `<img>` silently failed, and the draft came back with an image and
    `visual_error: None` — success reported for an asset with empty boxes in it.
    """
    untyped_slot_library(session)
    invented = {**WRITTEN, "visual_values": {"big_number": "40", "left_image_url": "image CTR"}}

    draft = generate_draft(
        session, FakeLLM(invented), FakeRenderer(), FakeImageRenderer(), idea=IDEA
    )

    assert "left_image_url" not in draft.visual_values
    assert draft.visual_image is None
    assert draft.visual_error is not None
    assert draft.body_text.startswith("not a rebrand")


# --- US-015: recorded verdicts feed generation as lessons ---------------------------------

# The prompt exactly as it was built before lessons existed, for the same fixture as
# `test_generation_is_grounded_in_the_posts_the_hook_came_from`. Captured from the code at
# the commit before this slice, not typed from the format by hand. It is here so "no
# verdicts changes nothing" is a byte comparison rather than a claim: a stray newline or a
# lessons header that leaks in when the list is empty fails this and nothing else would.
PROMPT_BEFORE_LESSONS = (
    "Idea:\n"
    "a seller tested one main image and got +17% CTR\n"
    "\n"
    "Hook pattern to follow:\n"
    "{small} turned into {large}\n"
    "Hook tone: plain, lowercase\n"
    "\n"
    "Structure (deep-research):\n"
    "1. result: Lead with the dollar outcome.\n"
    "\n"
    "Visual slots to fill: big_number, headline\n"
    "\n"
    "Exemplar posts — match this voice, not this content:\n"
    "---\n"
    "The exemplar post body that proves the pattern."
)


def rule(session, post: Post, verdict: Verdict, note: str) -> Post:
    """Record a human's ruling the way the verdict route does, without going through it."""
    post.verdict = verdict
    post.verdict_note = note
    post.verdict_at = datetime.now(UTC)
    session.add(post)
    session.flush()
    return post


def test_a_recorded_verdict_reaches_the_prompt_as_a_lesson(session):
    library(session)
    add_post(session, "win-1", 185, "The exemplar post body that proves the pattern.")
    judged = add_post(session, "judged-1", 300, "A post that already went out.")
    rule(session, judged, Verdict.WORKED, "the opening number did the work; the ask fell flat")

    llm = FakeLLM(WRITTEN)
    generate_draft(session, llm, FakeRenderer(), FakeImageRenderer(), idea=IDEA)

    assert "the opening number did the work; the ask fell flat" in llm.last_user
    assert "worked" in llm.last_user


def test_lessons_survive_a_regeneration(session):
    """The trap this slice exists to avoid: two call sites, one of them forgotten.

    Asserted at both — the prompt from `generate_draft` and the prompt from
    `regenerate_text` — because lessons that only reach the first are lessons that vanish
    the moment anyone presses regenerate.
    """
    library(session)
    judged = add_post(session, "judged-1", 300, "A post that already went out.")
    rule(session, judged, Verdict.DIDNT, "opened on the process, nobody stayed for the result")

    llm = FakeLLM(WRITTEN)
    draft = generate_draft(session, llm, FakeRenderer(), FakeImageRenderer(), idea=IDEA)
    generated_prompt = llm.last_user
    regenerate_text(session, llm, draft)
    regenerated_prompt = llm.last_user

    assert generated_prompt is not regenerated_prompt
    for prompt in (generated_prompt, regenerated_prompt):
        assert "opened on the process, nobody stayed for the result" in prompt
        assert "didnt" in prompt


def test_with_no_verdicts_recorded_the_prompt_is_byte_identical_to_before(session):
    """Checked at both call sites, for the same reason lessons are: one is not the other."""
    library(session)
    add_post(session, "win-1", 185, "The exemplar post body that proves the pattern.")

    llm = FakeLLM(WRITTEN)
    draft = generate_draft(session, llm, FakeRenderer(), FakeImageRenderer(), idea=IDEA)

    assert llm.last_user == PROMPT_BEFORE_LESSONS

    regenerate_text(session, llm, draft)

    assert llm.last_user == PROMPT_BEFORE_LESSONS


def test_a_verdict_with_no_note_changes_the_prompt_not_at_all(session):
    """A ruling with no reason is not a lesson.

    The lesson is the human's words; nothing else about the post is sent. So a note-less
    verdict would arrive as the bare word `worked` attached to no post the model can see —
    one tick in a tally, and a tally is the statistics reading that must never appear here.
    """
    library(session)
    add_post(session, "win-1", 185, "The exemplar post body that proves the pattern.")
    judged = add_post(session, "judged-1", 300, "A post that already went out.")
    rule(session, judged, Verdict.WORKED, "   ")

    llm = FakeLLM(WRITTEN)
    generate_draft(session, llm, FakeRenderer(), FakeImageRenderer(), idea=IDEA)

    assert llm.last_user == PROMPT_BEFORE_LESSONS


def test_a_lesson_from_a_creator_post_carries_the_note_and_never_the_writing(session):
    """Lessons may come from any judged post; voice still comes from one account only.

    A creator post here is judged *and* named in the hook's provenance *and* outranks
    Monte's on engagement — the full adversarial setup — and its text still never reaches
    the model. The human's note about it does, which is a person's words, not a voice.
    """
    hook, _, _ = library(session)
    hook.provenance = ["win-1", "theirs"]
    session.add(hook)
    session.flush()
    add_post(session, "win-1", 185, "Monte's own post body.")
    theirs = add_post(
        session, "theirs", 5000, "Someone else's writing.", author=settings.inspiration_account
    )
    rule(session, theirs, Verdict.MIXED, "the contrast opener is worth borrowing")

    llm = FakeLLM(WRITTEN)
    generate_draft(session, llm, FakeRenderer(), FakeImageRenderer(), idea=IDEA)

    assert "the contrast opener is worth borrowing" in llm.last_user
    assert "Someone else's writing." not in llm.last_user
    assert "Monte's own post body." in llm.last_user


def test_the_lessons_block_makes_no_claim_about_performance(session):
    """Asserted rather than trusted, the same way the scoreboard's payload is.

    A verdict is judgement at n=1. Wording that turns it into a ranking is the failure
    mode, and it is the kind of thing an edit reintroduces casually, so the words are
    pinned here.
    """
    library(session)
    judged = add_post(session, "judged-1", 300, "A post that already went out.")
    rule(session, judged, Verdict.WORKED, "the opening number did the work")

    llm = FakeLLM(WRITTEN)
    generate_draft(session, llm, FakeRenderer(), FakeImageRenderer(), idea=IDEA)

    lowered = llm.last_user.lower()
    for banned in ("best", "rank", "perform", "score", "top-", "average", "win rate"):
        assert banned not in lowered


def test_only_the_most_recently_judged_verdicts_are_carried(session):
    library(session)
    for index in range(LESSON_LIMIT + 3):
        judged = add_post(session, f"judged-{index}", 300, f"post {index}")
        rule(session, judged, Verdict.WORKED, f"ruling number {index}")

    llm = FakeLLM(WRITTEN)
    generate_draft(session, llm, FakeRenderer(), FakeImageRenderer(), idea=IDEA)

    assert llm.last_user.count("ruling number") == LESSON_LIMIT
    assert "ruling number 0" not in llm.last_user


# --- a redraw renders the version the draft names, not whatever is newest -----------------
#
# Every renderer here is `renderer_capturing` — a real `CloudflareRenderer` over an
# `httpx.MockTransport` that keeps the posted markup. `FakeRenderer` above discards its `html`
# argument, so under it a redraw rendering the wrong template would pass.


def edited_visual(session, visual, marker: str):
    """The next version of the visual family, distinguishable in the markup it renders."""
    return edit_template(
        session,
        visual,
        body={
            "renderer": "html",
            "html": f"<b>{{big_number}}</b><p>{{headline}}</p><i>{marker}</i>",
        },
    )


def test_a_redraw_renders_the_version_the_draft_was_generated_from(session):
    """The silent lineage corruption this closes.

    A draft generated from v2, then someone edits the template. The redraw used to render v3
    — `_current` is `order_by(version.desc()).first()` — and store that PNG while
    `visual_version` still said 2. `publishing.lineage_metadata` then pushed
    `visual_version: 2` to Zernio and `metrics.template_performance` credited v2 with a
    picture v3 produced. Nothing raised.

    Asserted in both directions on the posted markup: v2's marker present *and* v3's absent.
    """
    _, _, visual = library(session)
    v2 = edited_visual(session, visual, "v2")
    approve(session, v2)
    draft = generate_draft(
        session, FakeLLM(WRITTEN), FakeRenderer(), FakeImageRenderer(), idea=IDEA
    )
    assert draft.visual_version == v2.version

    edited_visual(session, v2, "v3")
    captured: dict = {}
    regenerate_visual(session, draft, renderer_capturing(captured, png=b"REDRAWN"))

    assert "<i>v2</i>" in captured["html"]
    assert "<i>v3</i>" not in captured["html"]
    # And the lineage still agrees with the picture, which is the whole point.
    assert draft.visual_version == v2.version
    assert draft.visual_image == b"REDRAWN"


def test_a_rewrite_uses_the_hook_the_draft_was_generated_from(session):
    """The same lineage defect as the redraw, one door along — and it was left behind.

    `regenerate_text` already promised "Lineage does not move", but it resolved each family by
    newest row, so editing a hook and pressing regenerate rewrote the words against v2's
    pattern while `hook_version` still said 1. `lineage_metadata` then pushed version 1 to
    Zernio and `template_performance` credited it with text a different template wrote. Three
    families were exposed, not one, and `_written_values` took the newest visual's slots too.

    v2 has to exist for this to prove anything — in a single-version family the fix and the
    bug are indistinguishable.
    """
    hook, _, _ = library(session)
    llm = FakeLLM(WRITTEN)
    draft = generate_draft(session, llm, FakeRenderer(), FakeImageRenderer(), idea=IDEA)
    recorded = draft.hook_version

    v2 = edit_template(
        session,
        hook,
        body={"pattern": "{small} became {large}, eventually", "tone": "plain, lowercase"},
    )
    approve(session, v2)

    regenerate_text(session, llm, draft)

    assert "{small} turned into {large}" in llm.last_user
    assert "eventually" not in llm.last_user
    # And the column did not move either, so metadata and prompt describe one template.
    assert draft.hook_version == recorded


def test_a_retired_recorded_version_still_redraws(session):
    """A redraw is a re-render of what this draft already is, not a new generation.

    So the version its lineage names is the honest picture even once that version is
    withdrawn. `usable_templates` is what keeps a retired template out of everything that
    *chooses* one; refusing here would instead mean an operator retiring a template silently
    broke the redraw button on every draft already built from it.

    A newer live version has to exist for this to prove anything — with one version in the
    family, "recorded" and "latest" are the same row and the old behaviour would pass. So:
    v2 makes the draft, v3 is authored, *then* v2 is retired. `edit_template` refuses a
    retired row, which is why the retirement comes last.
    """
    _, _, visual = library(session)
    v2 = edited_visual(session, visual, "v2")
    approve(session, v2)
    draft = generate_draft(
        session, FakeLLM(WRITTEN), FakeRenderer(), FakeImageRenderer(), idea=IDEA
    )
    edited_visual(session, v2, "v3")
    retire(session, v2)

    captured: dict = {}
    regenerate_visual(session, draft, renderer_capturing(captured, png=b"STILLDRAWN"))

    assert draft.visual_error is None
    assert draft.visual_image == b"STILLDRAWN"
    assert "<i>v2</i>" in captured["html"]
    assert "<i>v3</i>" not in captured["html"]


def test_the_renderer_is_chosen_from_the_recorded_version_too(session):
    """Picking the renderer off the latest version is the same bug one column over.

    A draft made from a v2 that declares `renderer: "html"` must redraw through the HTML
    renderer even after v3 switches the family to `ai` — otherwise the redraw hands an HTML
    template to the image model, or the reverse, on the strength of an edit the draft has no
    part in.
    """
    _, _, visual = library(session)
    v2 = edited_visual(session, visual, "v2")
    approve(session, v2)
    draft = generate_draft(
        session, FakeLLM(WRITTEN), FakeRenderer(), FakeImageRenderer(), idea=IDEA
    )
    edit_template(session, v2, body={"renderer": "ai", "prompt": "a picture of {big_number}"})

    html_renderer, image_renderer = object(), object()

    assert _renderer(session, draft, html_renderer, image_renderer) is html_renderer


def test_a_draft_recording_no_version_refuses_rather_than_picking_one(session):
    """The other half of `generated_from`'s guard, and it was uncovered: deleting the
    `version is None` branch left the suite green, and without it the query becomes
    `Template.version == None`, which matches nothing and reports "v None no longer exists" —
    a message about a version the draft never had. The two states are different facts.
    """
    _, _, visual = library(session)
    draft = generate_draft(
        session, FakeLLM(WRITTEN), FakeRenderer(), FakeImageRenderer(), idea=IDEA
    )
    draft.visual_version = None
    session.flush()

    with pytest.raises(NoUsableTemplates, match="records no version"):
        regenerate_visual(session, draft, renderer_capturing({}))


def test_a_recorded_version_that_is_gone_refuses_rather_than_redrawing_another(session):
    """The one case a redraw cannot be faithful in. It must not fall back to a sibling
    version — that is exactly the mis-attribution being fixed."""
    _, _, visual = library(session)
    v2 = edited_visual(session, visual, "v2")
    approve(session, v2)
    draft = generate_draft(
        session, FakeLLM(WRITTEN), FakeRenderer(), FakeImageRenderer(), idea=IDEA
    )

    session.delete(v2)
    session.flush()

    with pytest.raises(NoUsableTemplates):
        regenerate_visual(session, draft, renderer_capturing({}))


def test_the_redraw_endpoint_reports_a_vanished_version_as_a_conflict(client, session):
    """A 409 like generation's, not a 500: the library cannot serve this draft's version."""
    _, _, visual = library(session)
    v2 = edited_visual(session, visual, "v2")
    approve(session, v2)
    draft = generate_draft(
        session, FakeLLM(WRITTEN), FakeRenderer(), FakeImageRenderer(), idea=IDEA
    )
    session.delete(v2)
    session.flush()

    response = client.post(f"/drafts/{draft.id}/regenerate-visual")

    assert response.status_code == 409
    assert visual.family_id in response.json()["detail"]


# --- US-014: re-topic — same templates, new subject ------------------------------------

# **An opinion, deliberately.** `resolve_mode` reads a named subject and a number as a factual
# floor, and the previous subject ("a seller swapped their hero shot and lost 4% of their
# click-through") resolves to `light` — so once re-topic began running the reviewed workflow,
# every test below stopped at a failed research state and its lineage assertions became
# assertions about a `failed` row. The factual case is worth having and has its own test
# rather than being every test's silent background: see
# `test_a_factual_retopic_with_no_search_provider_stops_visibly`.
NEW_IDEA = "why most rebrands are a distraction from the offer"
FACTUAL_IDEA = "a seller swapped their hero shot and lost 4% of their click-through"

RETOPICKED = {
    "hook": "One swapped hero shot cost 4% of the clicks.",
    "body": "same listing. same price. a different picture.",
    "visual_values": {"big_number": "-4%", "headline": "One image. Every month."},
}


def next_version_of_everything(session, hook, structure, visual):
    """An approved v2 of all three families, each distinguishable in the prompt it builds.

    A re-topic test proves nothing against a single-version family: "the version recorded"
    and "the newest version" are then the same row, and the bug this slice exists to avoid
    passes. All three, because `regenerate_text` had the defect on all three at once — a
    re-topic resolving `structure` by latest while hook and visual were right would slip
    through a two-family assertion.
    """
    hook_v2 = edit_template(
        session,
        hook,
        body={"pattern": "{small} became {large}, eventually", "tone": "plain, lowercase"},
    )
    structure_v2 = edit_template(
        session,
        structure,
        body={
            "post_type": "deep-research",
            "sections": [{"name": "result", "guidance": "Bury the lede."}],
        },
    )
    visual_v2 = edited_visual(session, visual, "v2")
    for template in (hook_v2, structure_v2, visual_v2):
        approve(session, template)
    return hook_v2, structure_v2, visual_v2


def snapshot(draft) -> dict:
    """The source's whole state as plain values, read before the call.

    Values rather than the ORM instance on purpose: comparing attributes on the object
    afterwards compares a row to itself once SQLAlchemy has refreshed it, and would pass
    against a re-topic that overwrote the source in place.
    """
    return {
        "idea": draft.idea,
        "hook_family": draft.hook_family,
        "hook_version": draft.hook_version,
        "structure_family": draft.structure_family,
        "structure_version": draft.structure_version,
        "visual_family": draft.visual_family,
        "visual_version": draft.visual_version,
        "hook_text": draft.hook_text,
        "body_text": draft.body_text,
        "visual_values": dict(draft.visual_values),
        "asset_values": dict(draft.asset_values),
        "visual_image": draft.visual_image,
        "zernio_post_id": draft.zernio_post_id,
        "pushed_at": draft.pushed_at,
        "went_live_at": draft.went_live_at,
    }


def test_a_retopic_inherits_the_sources_own_versions_not_the_newest(session):
    """Nikhil's pillar, and the one way it can be silently wrong.

    The templates are the point — a new subject written through the exact hook, structure
    and visual a past draft used. Resolving any of the three by newest version is the G2
    defect one door along: the new draft would be written by v2 while its lineage claimed
    v1, so `lineage_metadata` would push v1 to Zernio and `template_performance` would
    credit v1 with v2's work.

    Asserted on what the model was actually sent, not only on the columns copied: v1's
    pattern and guidance present, v2's absent, and the visual rendered without v2's marker.
    """
    hook, structure, visual = library(session)
    source = generate_draft(
        session, FakeLLM(WRITTEN), FakeRenderer(), FakeImageRenderer(), idea=IDEA
    )
    recorded = (
        (source.hook_family, source.hook_version),
        (source.structure_family, source.structure_version),
        (source.visual_family, source.visual_version),
    )
    hook_v2, structure_v2, visual_v2 = next_version_of_everything(session, hook, structure, visual)

    llm = WorkflowLLM(RETOPICKED)
    captured: dict = {}
    fresh = retopic(
        session,
        llm,
        NoSearch(),
        renderer_capturing(captured, png=b"RETOPIC"),
        FakeImageRenderer(),
        source,
        idea=NEW_IDEA,
    )

    assert fresh.id != source.id
    assert (
        (fresh.hook_family, fresh.hook_version),
        (fresh.structure_family, fresh.structure_version),
        (fresh.visual_family, fresh.visual_version),
    ) == recorded
    # There really is a newer version of each family, so "recorded" and "latest" differ.
    assert (hook_v2.version, structure_v2.version, visual_v2.version) == (2, 2, 2)
    assert fresh.hook_version == 1
    # The templates that actually built the draft, not merely the columns on it.
    assert "{small} turned into {large}" in llm.last_write
    assert "eventually" not in llm.last_write
    assert "Lead with the dollar outcome." in llm.last_write
    assert "Bury the lede." not in llm.last_write
    assert "<i>v2</i>" not in captured["html"]
    # And it is a new subject, written new.
    assert fresh.idea == NEW_IDEA
    assert NEW_IDEA in llm.last_write
    assert fresh.hook_text.startswith("One swapped hero shot")


def test_a_retopic_leaves_the_source_untouched(session):
    """The source is read, never written. Its lineage, its words and its metrics hold.

    A re-topic that moved the source would destroy the attribution record it was chosen
    for — and `regenerate_text` already exists for the case where rewriting in place is
    what was wanted.
    """
    library(session)
    source = generate_draft(
        session, FakeLLM(WRITTEN), FakeRenderer(), FakeImageRenderer(), idea=IDEA
    )
    source.zernio_post_id = "late-abc"
    source.pushed_at = datetime(2026, 7, 1, tzinfo=UTC).replace(tzinfo=None)
    source.went_live_at = datetime(2026, 7, 2, tzinfo=UTC).replace(tzinfo=None)
    session.add(source)
    session.flush()
    before = snapshot(source)

    fresh = retopic(
        session,
        WorkflowLLM(RETOPICKED),
        NoSearch(),
        FakeRenderer(b"NEW"),
        FakeImageRenderer(),
        source,
        idea=NEW_IDEA,
    )

    assert fresh.id != source.id
    assert snapshot(source) == before
    # And the new row is genuinely a different draft, not a view of the old one.
    assert fresh.idea != source.idea
    assert fresh.hook_text != source.hook_text
    assert fresh.zernio_post_id is None
    assert fresh.went_live_at is None


def test_a_retopic_from_a_retired_version_still_works(session):
    """The version history is the attribution record — pruning it was refused in V2.

    A source built on v1 must still be re-topickable once v1 is retired, exactly as a
    retired version still redraws: `usable_templates` is what keeps a withdrawn template out
    of everything that *chooses* one, and a re-topic chooses nothing. Ordering matters —
    `edit_template` refuses a retired row, so v2 is authored before v1 is retired.
    """
    hook, structure, visual = library(session)
    source = generate_draft(
        session, FakeLLM(WRITTEN), FakeRenderer(), FakeImageRenderer(), idea=IDEA
    )
    next_version_of_everything(session, hook, structure, visual)
    for template in (hook, structure, visual):
        retire(session, template)
    # Stated rather than assumed: `edit_template` carries status forward, so without this the
    # test would still pass if the versions the source records were merely superseded.
    assert [
        generated_from(session, family, 1).status
        for family in (hook.family_id, structure.family_id, visual.family_id)
    ] == [TemplateStatus.RETIRED] * 3

    llm = WorkflowLLM(RETOPICKED)
    captured: dict = {}
    fresh = retopic(
        session,
        llm,
        NoSearch(),
        renderer_capturing(captured, png=b"RETIRED"),
        FakeImageRenderer(),
        source,
        idea=NEW_IDEA,
    )

    assert (fresh.hook_version, fresh.structure_version, fresh.visual_version) == (1, 1, 1)
    assert "{small} turned into {large}" in llm.last_write
    assert "<i>v2</i>" not in captured["html"]
    assert fresh.visual_error is None
    assert fresh.visual_image == b"RETIRED"


def test_the_retopic_endpoint_creates_a_new_draft_and_says_what_it_spent(client, session):
    hook, structure, visual = library(session)
    source = generate_draft(
        session, FakeLLM(WRITTEN), FakeRenderer(), FakeImageRenderer(), idea=IDEA
    )
    next_version_of_everything(session, hook, structure, visual)
    app.dependency_overrides[get_llm] = lambda: WorkflowLLM(RETOPICKED)
    app.dependency_overrides[get_html_renderer] = FakeRenderer
    app.dependency_overrides[get_search] = NoSearch

    response = client.post("/drafts/retopic", json={"idea": NEW_IDEA, "source_draft_id": source.id})

    assert response.status_code == 201
    body = response.json()
    assert body["id"] != source.id
    assert body["idea"] == NEW_IDEA
    # The lineage the frontend reads, resolved through `generated_from`, not the newest row.
    assert body["lineage"]["hook"]["version"] == 1
    assert body["lineage"]["structure"]["version"] == 1
    assert body["lineage"]["visual"]["version"] == 1
    # The picture survives the commit/refresh/base64/`model_dump()` round-trip this route
    # takes and `regenerate-visual` does not — the visual is what a re-topic is for.
    assert body["visual_png"]
    assert body["visual_error"] is None
    # Reviewed, not merely rewritten. The templates are inherited and the editorial work is
    # not: this row got its own brief, angle, gates and readiness evaluation, which is what
    # makes it pushable at all — `Draft.generation_stage` defaults to `unreviewed`.
    assert body["generation_stage"] == GenerationStage.READY
    # Observed spend, through the one meter — not predicted from the request. Four
    # completions, because a re-topic is a whole workflow now and not one write.
    assert (body["llm_calls"], body["image_calls"]) == (WORKFLOW_CALLS, 1)
    assert body["search_calls"] == 0


def test_a_retopic_can_start_from_a_published_post(client, session):
    """ "Take any past post" — the post page's entry point, resolved through its draft.

    The join is `Draft.zernio_post_id == Post.late_post_id` (`metrics.draft_for_post`), the
    same one the Inbox and the scoreboard use.
    """
    library(session)
    source = generate_draft(
        session, FakeLLM(WRITTEN), FakeRenderer(), FakeImageRenderer(), idea=IDEA
    )
    source.zernio_post_id = "late-xyz"
    post = add_post(session, "z-retopic", 900, "what went out")
    post.late_post_id = "late-xyz"
    session.add_all([source, post])
    session.flush()
    app.dependency_overrides[get_llm] = lambda: WorkflowLLM(RETOPICKED)
    app.dependency_overrides[get_html_renderer] = FakeRenderer
    app.dependency_overrides[get_search] = NoSearch

    response = client.post("/drafts/retopic", json={"idea": NEW_IDEA, "source_post_id": post.id})

    assert response.status_code == 201
    body = response.json()
    assert body["id"] != source.id
    assert body["lineage"]["hook"]["family"] == source.hook_family
    assert body["lineage"]["hook"]["version"] == source.hook_version


def test_a_post_this_app_did_not_generate_has_no_templates_to_inherit(client, session):
    """Most of the corpus was written outside this app and carries no lineage at all."""
    library(session)
    post = add_post(session, "z-ingested", 400, "ingested from the account's history")

    response = client.post("/drafts/retopic", json={"idea": NEW_IDEA, "source_post_id": post.id})

    assert response.status_code == 409
    assert str(post.id) in response.json()["detail"]


def test_pushing_an_unknown_draft_says_which_one(client, session):
    """`POST /drafts/{id}/push` had no HTTP test at all — renaming the route away left the
    whole backend suite green. `push_draft` itself is covered in test_publishing.py; the route
    around it, which is what Studio's one irreversible button calls, was not.
    """
    response = client.post("/drafts/987654/push")

    assert response.status_code == 404
    assert response.json()["detail"] == "no draft 987654"


def test_a_refused_push_is_a_502_carrying_zernios_reason(client, session):
    """`PushFailed` -> 502, with the reason. A push that failed and answered 200 would leave
    the draft in Inbox queue 2 while the operator believes it reached Zernio.
    """
    library(session)
    with_fakes()
    draft = client.post("/drafts", json={"idea": IDEA}).json()
    # `POST /drafts` is the deprecated unreviewed path, so the push boundary would refuse this
    # with a 409 before Zernio was ever called — and the refusal under test is Zernio's. Marked
    # review-ready by hand so the request gets far enough to be refused for the right reason.
    stored = session.get(Draft, draft["id"])
    stored.generation_stage = GenerationStage.READY
    session.commit()

    class Refusing:
        # The draft carries a rendered visual, so the push uploads before it creates. The
        # upload succeeds here so the refusal under test is still the create's.
        def upload_media(self, *args, **kwargs):
            return "https://media.zernio.test/temp/x.png"

        def create_post(self, *args, **kwargs):
            raise PushFailed("Zernio returned no post id")

    app.dependency_overrides[get_zernio] = Refusing
    try:
        response = client.post(f"/drafts/{draft['id']}/push")
    finally:
        app.dependency_overrides.pop(get_zernio, None)

    assert response.status_code == 502
    assert "no post id" in response.json()["detail"]
    # And nothing was recorded: an unpushed draft must not carry a Zernio id.
    assert client.get(f"/drafts/{draft['id']}").json()["zernio_post_id"] is None


def test_suggesting_with_nothing_approved_is_a_conflict(client, session):
    """`POST /drafts/suggest` had no HTTP test either. Studio calls it before every generate,
    and a 409 is what the empty-library state depends on — `NoUsableTemplates` reaching the
    client as a 500 would render as "request failed" instead of the reason.
    """
    with_fakes()

    response = client.post("/drafts/suggest", json={"idea": IDEA})

    assert response.status_code == 409
    assert response.json()["detail"] != ""


def test_reading_an_unknown_draft_says_which_one(client, session):
    """`GET /drafts/{id}` had happy-path readers in three files and no 404 test anywhere.

    Studio renders this detail verbatim — `?draft=999` shows "HTTP 404: no draft 999" — so the
    sentence is a contract, not an implementation detail. The status alone would not do: an
    unrouted path answers 404 too.
    """
    response = client.get("/drafts/987654")

    assert response.status_code == 404
    assert response.json()["detail"] == "no draft 987654"


def test_the_retopic_endpoint_needs_exactly_one_source(client, session):
    library(session)

    assert client.post("/drafts/retopic", json={"idea": NEW_IDEA}).status_code == 422
    assert (
        client.post(
            "/drafts/retopic",
            json={"idea": NEW_IDEA, "source_draft_id": 1, "source_post_id": 1},
        ).status_code
        == 422
    )
    unknown = client.post("/drafts/retopic", json={"idea": NEW_IDEA, "source_draft_id": 987654})
    assert unknown.status_code == 404
    # The 422s above already prove the route is mounted; this names which id was refused.
    assert unknown.json()["detail"] == "no draft 987654"


def test_a_retopic_from_a_vanished_version_is_a_conflict_not_a_redraw_of_another(client, session):
    """A 409 like the redraw's: the library cannot serve this draft's version, and falling
    back to a sibling is the mis-attribution the whole slice is about."""
    _, _, visual = library(session)
    source = generate_draft(
        session, FakeLLM(WRITTEN), FakeRenderer(), FakeImageRenderer(), idea=IDEA
    )
    edited_visual(session, visual, "v2")
    session.delete(visual)
    session.flush()
    app.dependency_overrides[get_llm] = lambda: WorkflowLLM(RETOPICKED)
    app.dependency_overrides[get_html_renderer] = FakeRenderer
    app.dependency_overrides[get_search] = NoSearch

    response = client.post("/drafts/retopic", json={"idea": NEW_IDEA, "source_draft_id": source.id})

    assert response.status_code == 409
    assert visual.family_id in response.json()["detail"]


# --- US-013: one idea, several drafts, keep one -------------------------------------------


def variant_library(session):
    """Nine approved templates — three of each kind — so supply exceeds any ceiling here.

    `library()` above authors exactly one of each, which yields exactly one combination: every
    clamp assertion below would then pass on template supply rather than on the bound, which
    is the way the autonomous clamp test was nearly written too ("pinned to 1 so three
    available topics can prove the bound rather than the topic supply doing it").
    """
    library(session)
    for index in (2, 3):
        for template in (
            create_template(
                session,
                kind=TemplateKind.HOOK,
                name=f"hook-{index}",
                body={"pattern": f"pattern {index}", "tone": "plain"},
            ),
            create_template(
                session,
                kind=TemplateKind.STRUCTURE,
                name=f"structure-{index}",
                body={
                    "post_type": "deep-research",
                    "sections": [{"name": "result", "guidance": f"guidance {index}"}],
                },
            ),
            create_template(
                session,
                kind=TemplateKind.VISUAL,
                name=f"visual-{index}",
                body={"renderer": "html", "html": f"<b>{{big_number}}</b><i>{index}</i>"},
                slots=[{"name": "big_number", "type": "text"}],
            ),
        ):
            approve(session, template)


def all_drafts(session) -> list[Draft]:
    return list(session.exec(select(Draft).order_by(col(Draft.id))).all())


# An opinion, for the same reason `NEW_IDEA` is one: a variants batch runs N reviewed
# workflows, and a factual idea with no search provider would make every count and spend
# assertion below an assertion about N failed research states. The factual case has its own
# test.
VARIANT_IDEA = "why most rebrands are a distraction from the offer"

def variants_of(client, count: int | None = None, idea: str = VARIANT_IDEA) -> dict:
    body: dict = {"idea": idea}
    if count is not None:
        body["count"] = count
    response = client.post("/drafts/variants", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def with_fakes():
    """The three adapters a variants or re-topic request now takes.

    `WorkflowLLM`, not `FakeLLM`: both routes run the reviewed workflow, and a stub answering
    the brief with a written post fails planning silently. `get_search` is overridden too —
    without it these requests build the real provider from settings, which in a test
    environment is `NoSearchProvider`, and a `none`-mode idea would pass only by never
    reaching it. Overriding says what is being assumed.
    """
    app.dependency_overrides[get_llm] = lambda: WorkflowLLM(WRITTEN)
    app.dependency_overrides[get_html_renderer] = FakeRenderer
    app.dependency_overrides[get_search] = NoSearch


def test_variants_move_all_three_templates_not_only_the_visual(session):
    """Three drafts of one idea, each from a different combination.

    The rotation is what makes this true of all three axes. Walking the cartesian product
    instead would hand back three drafts sharing a hook and a structure — three near-identical
    posts at three times the price, which is the opposite of what a comparison is for.
    """
    variant_library(session)

    combos = variant_combinations(session, 3)

    assert len(combos) == 3
    assert len({(h.id, s.id, v.id) for h, s, v in combos}) == 3
    assert len({h.id for h, _, _ in combos}) == 3
    assert len({s.id for _, s, _ in combos}) == 3
    assert len({v.id for _, _, v in combos}) == 3


def test_a_library_offering_one_combination_yields_one_variant(session):
    """Three copies of the same combination is three times the spend for one draft."""
    library(session)

    assert len(variant_combinations(session, 3)) == 1


def test_an_over_large_variant_request_is_clamped_to_the_ceiling(client, session, monkeypatch):
    """The ceiling is a bound, not a default — the `?cap=500` lesson, one route over.

    Pinned to 2 against a default of 3 and a library offering 9 combinations, so neither the
    setting's default nor the template supply can be what makes this pass. Asserted on the
    rows written and on the meter as well as on the list returned: a route that generated 50
    drafts and answered with 2 of them would pass a length check on the response alone.

    The meter is read as a multiple of the workflow's length rather than as one call per
    variant, which is the point of clamping this route at all: a variant is four completions
    and a render now, so an unclamped `count` is that much worse than it was.
    """
    monkeypatch.setattr(settings, "variants_max", 2)
    variant_library(session)
    with_fakes()

    body = variants_of(client, count=50)

    assert len(body["variants"]) == settings.variants_max
    assert len(all_drafts(session)) == settings.variants_max
    assert body["llm_calls"] == settings.variants_max * WORKFLOW_CALLS


def test_a_variant_count_below_the_ceiling_is_still_honoured(client, session, monkeypatch):
    """A ceiling, not a fixed number: asking for less must still mean less."""
    monkeypatch.setattr(settings, "variants_max", 3)
    variant_library(session)
    with_fakes()

    assert len(variants_of(client, count=1)["variants"]) == 1
    assert len(all_drafts(session)) == 1


def test_asking_for_no_count_at_all_generates_the_configured_number(client, session, monkeypatch):
    monkeypatch.setattr(settings, "variants_max", 2)
    variant_library(session)
    with_fakes()

    assert len(variants_of(client)["variants"]) == 2


def test_the_variants_endpoint_reports_the_spend_for_the_whole_batch(client, session, monkeypatch):
    """US-011, for a route that multiplies the bill by N."""
    monkeypatch.setattr(settings, "variants_max", 3)
    variant_library(session)
    with_fakes()

    body = variants_of(client)

    # Four completions per variant — brief, angle, write, rubric — and one render each. No
    # suggest call: this route names all three templates itself. Three renders is what says
    # every variant reached the end rather than stopping at a failed review.
    assert (body["llm_calls"], body["image_calls"]) == (12, 3)
    # Zero searches, honestly: `VARIANT_IDEA` resolves to `none`, which never reaches a
    # provider. The key is reported rather than omitted because a factual idea here would
    # buy real searches — which was not true before this route ran the workflow.
    assert body["search_calls"] == 0
    assert all(v["visual_png"] for v in body["variants"])
    # Reviewed, not merely written. This is the property the whole change is for: a variant
    # is keepable because it passed, not because a model returned text.
    assert all(v["generation_stage"] == GenerationStage.READY for v in body["variants"])


def test_nothing_in_the_variants_response_ranks_them(client, session, monkeypatch):
    """Generation order, and no second ordering anywhere in the body.

    Engagement spans 12.7x at ~3 samples per template, so no field this endpoint could add
    would mean anything — a score, a confidence or a "recommended" flag would all be the
    optimizer, which is a threshold (~300 lineage-tagged posts, currently zero) and not a
    feature. Asserted as an exact key set rather than by hunting for names, so adding one
    later has to be a deliberate act that breaks this test.
    """
    monkeypatch.setattr(settings, "variants_max", 3)
    variant_library(session)
    with_fakes()

    body = variants_of(client)

    ids = [v["id"] for v in body["variants"]]
    assert ids == sorted(ids)
    assert set(body) == {"variants", "llm_calls", "image_calls", "search_calls"}
    assert set(body["variants"][0]) == set(DraftOut.model_fields)


def test_keeping_one_variant_deletes_the_others(client, session, monkeypatch):
    """Discarded means gone. A rejected variant left behind makes Inbox queue 2 lie.

    "Built, awaiting push" is a human work queue and one of the four gates the Inbox exists
    to keep honest, so two variants nobody chose sitting in it is not a cosmetic problem.
    """
    monkeypatch.setattr(settings, "variants_max", 3)
    variant_library(session)
    with_fakes()
    kept, *rest = variants_of(client)["variants"]

    response = client.post(
        "/drafts/variants/keep",
        json={"keep_id": kept["id"], "discard_ids": [v["id"] for v in rest]},
    )

    assert response.status_code == 200
    assert response.json()["id"] == kept["id"]
    assert [d.id for d in all_drafts(session)] == [kept["id"]]
    assert client.get("/inbox").json()["built_awaiting_push"]["count"] == 1


def test_a_variant_already_in_zernio_is_never_deleted(client, session, monkeypatch):
    """A draft that left the building has something out there pointing back at it."""
    monkeypatch.setattr(settings, "variants_max", 3)
    variant_library(session)
    with_fakes()
    kept, pushed, other = variants_of(client)["variants"]
    session.get(Draft, pushed["id"]).zernio_post_id = "late-abc"
    session.commit()

    response = client.post(
        "/drafts/variants/keep",
        json={"keep_id": kept["id"], "discard_ids": [pushed["id"], other["id"]]},
    )

    assert response.status_code == 409
    assert str(pushed["id"]) in response.json()["detail"]
    # Nothing at all was deleted — including the one that could have been.
    assert len(all_drafts(session)) == 3


def test_an_unknown_discard_id_deletes_nothing(client, session, monkeypatch):
    """Every id is checked before any row is removed, or a 404 on the third leaves the first
    two already gone inside the same request."""
    monkeypatch.setattr(settings, "variants_max", 3)
    variant_library(session)
    with_fakes()
    kept, *rest = variants_of(client)["variants"]

    response = client.post(
        "/drafts/variants/keep",
        json={"keep_id": kept["id"], "discard_ids": [rest[0]["id"], 987654]},
    )

    assert response.status_code == 404
    # The detail as well as the status: an unrouted path answers 404 and leaves the three rows
    # untouched too, so both assertions above pass with this route deleted. Measured.
    assert response.json()["detail"] == "no draft 987654"
    assert len(all_drafts(session)) == 3


def test_keep_refuses_to_discard_the_draft_it_is_keeping(client, session, monkeypatch):
    monkeypatch.setattr(settings, "variants_max", 3)
    variant_library(session)
    with_fakes()
    kept, *rest = variants_of(client)["variants"]

    response = client.post(
        "/drafts/variants/keep",
        json={"keep_id": kept["id"], "discard_ids": [kept["id"], rest[0]["id"]]},
    )

    assert response.status_code == 422
    assert len(all_drafts(session)) == 3
