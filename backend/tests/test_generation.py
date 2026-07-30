from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.api_drafts import _renderer
from app.config import settings
from app.db import get_session
from app.deps import get_html_renderer, get_llm
from app.generation import (
    LESSON_LIMIT,
    NoUsableTemplates,
    generate_draft,
    generated_from,
    regenerate_text,
    regenerate_visual,
    retopic,
    suggest_templates,
    writable_slots,
)
from app.main import app
from app.models.post import Post, Verdict
from app.models.template import TemplateKind, TemplateStatus
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
    generate_draft(session, llm, FakeRenderer(), idea=IDEA)

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
    draft = generate_draft(session, llm, FakeRenderer(), idea=IDEA)
    regenerate_text(session, llm, draft)

    assert "Someone else's writing." not in llm.last_user


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

    draft = generate_draft(session, FakeLLM(invented), FakeRenderer(), idea=IDEA)

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
    generate_draft(session, llm, FakeRenderer(), idea=IDEA)

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
    draft = generate_draft(session, llm, FakeRenderer(), idea=IDEA)
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
    draft = generate_draft(session, llm, FakeRenderer(), idea=IDEA)

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
    generate_draft(session, llm, FakeRenderer(), idea=IDEA)

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
    generate_draft(session, llm, FakeRenderer(), idea=IDEA)

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
    generate_draft(session, llm, FakeRenderer(), idea=IDEA)

    lowered = llm.last_user.lower()
    for banned in ("best", "rank", "perform", "score", "top-", "average", "win rate"):
        assert banned not in lowered


def test_only_the_most_recently_judged_verdicts_are_carried(session):
    library(session)
    for index in range(LESSON_LIMIT + 3):
        judged = add_post(session, f"judged-{index}", 300, f"post {index}")
        rule(session, judged, Verdict.WORKED, f"ruling number {index}")

    llm = FakeLLM(WRITTEN)
    generate_draft(session, llm, FakeRenderer(), idea=IDEA)

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
    draft = generate_draft(session, FakeLLM(WRITTEN), FakeRenderer(), idea=IDEA)
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
    draft = generate_draft(session, llm, FakeRenderer(), idea=IDEA)
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
    draft = generate_draft(session, FakeLLM(WRITTEN), FakeRenderer(), idea=IDEA)
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
    draft = generate_draft(session, FakeLLM(WRITTEN), FakeRenderer(), idea=IDEA)
    edit_template(session, v2, body={"renderer": "ai", "prompt": "a picture of {big_number}"})

    html_renderer, image_renderer = object(), object()

    assert _renderer(session, draft, html_renderer, image_renderer) is html_renderer


def test_a_recorded_version_that_is_gone_refuses_rather_than_redrawing_another(session):
    """The one case a redraw cannot be faithful in. It must not fall back to a sibling
    version — that is exactly the mis-attribution being fixed."""
    _, _, visual = library(session)
    v2 = edited_visual(session, visual, "v2")
    approve(session, v2)
    draft = generate_draft(session, FakeLLM(WRITTEN), FakeRenderer(), idea=IDEA)

    session.delete(v2)
    session.flush()

    with pytest.raises(NoUsableTemplates):
        regenerate_visual(session, draft, renderer_capturing({}))


def test_the_redraw_endpoint_reports_a_vanished_version_as_a_conflict(client, session):
    """A 409 like generation's, not a 500: the library cannot serve this draft's version."""
    _, _, visual = library(session)
    v2 = edited_visual(session, visual, "v2")
    approve(session, v2)
    draft = generate_draft(session, FakeLLM(WRITTEN), FakeRenderer(), idea=IDEA)
    session.delete(v2)
    session.flush()

    response = client.post(f"/drafts/{draft.id}/regenerate-visual")

    assert response.status_code == 409
    assert visual.family_id in response.json()["detail"]


# --- US-014: re-topic — same templates, new subject ------------------------------------

NEW_IDEA = "a seller swapped their hero shot and lost 4% of their click-through"

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
    source = generate_draft(session, FakeLLM(WRITTEN), FakeRenderer(), idea=IDEA)
    recorded = (
        (source.hook_family, source.hook_version),
        (source.structure_family, source.structure_version),
        (source.visual_family, source.visual_version),
    )
    hook_v2, structure_v2, visual_v2 = next_version_of_everything(
        session, hook, structure, visual
    )

    llm = FakeLLM(RETOPICKED)
    captured: dict = {}
    fresh = retopic(
        session, llm, renderer_capturing(captured, png=b"RETOPIC"), source, idea=NEW_IDEA
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
    assert "{small} turned into {large}" in llm.last_user
    assert "eventually" not in llm.last_user
    assert "Lead with the dollar outcome." in llm.last_user
    assert "Bury the lede." not in llm.last_user
    assert "<i>v2</i>" not in captured["html"]
    # And it is a new subject, written new.
    assert fresh.idea == NEW_IDEA
    assert NEW_IDEA in llm.last_user
    assert fresh.hook_text.startswith("One swapped hero shot")


def test_a_retopic_leaves_the_source_untouched(session):
    """The source is read, never written. Its lineage, its words and its metrics hold.

    A re-topic that moved the source would destroy the attribution record it was chosen
    for — and `regenerate_text` already exists for the case where rewriting in place is
    what was wanted.
    """
    library(session)
    source = generate_draft(session, FakeLLM(WRITTEN), FakeRenderer(), idea=IDEA)
    source.zernio_post_id = "late-abc"
    source.pushed_at = datetime(2026, 7, 1, tzinfo=UTC).replace(tzinfo=None)
    source.went_live_at = datetime(2026, 7, 2, tzinfo=UTC).replace(tzinfo=None)
    session.add(source)
    session.flush()
    before = snapshot(source)

    fresh = retopic(session, FakeLLM(RETOPICKED), FakeRenderer(b"NEW"), source, idea=NEW_IDEA)

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
    source = generate_draft(session, FakeLLM(WRITTEN), FakeRenderer(), idea=IDEA)
    next_version_of_everything(session, hook, structure, visual)
    for template in (hook, structure, visual):
        retire(session, template)
    # Stated rather than assumed: `edit_template` carries status forward, so without this the
    # test would still pass if the versions the source records were merely superseded.
    assert [
        generated_from(session, family, 1).status
        for family in (hook.family_id, structure.family_id, visual.family_id)
    ] == [TemplateStatus.RETIRED] * 3

    llm = FakeLLM(RETOPICKED)
    captured: dict = {}
    fresh = retopic(
        session, llm, renderer_capturing(captured, png=b"RETIRED"), source, idea=NEW_IDEA
    )

    assert (fresh.hook_version, fresh.structure_version, fresh.visual_version) == (1, 1, 1)
    assert "{small} turned into {large}" in llm.last_user
    assert "<i>v2</i>" not in captured["html"]
    assert fresh.visual_error is None
    assert fresh.visual_image == b"RETIRED"


def test_the_retopic_endpoint_creates_a_new_draft_and_says_what_it_spent(client, session):
    hook, structure, visual = library(session)
    source = generate_draft(session, FakeLLM(WRITTEN), FakeRenderer(), idea=IDEA)
    next_version_of_everything(session, hook, structure, visual)
    app.dependency_overrides[get_llm] = lambda: FakeLLM(RETOPICKED)
    app.dependency_overrides[get_html_renderer] = FakeRenderer

    response = client.post(
        "/drafts/retopic", json={"idea": NEW_IDEA, "source_draft_id": source.id}
    )

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
    # Observed spend, through the one meter — not predicted from the request.
    assert (body["llm_calls"], body["image_calls"]) == (1, 1)


def test_a_retopic_can_start_from_a_published_post(client, session):
    """"Take any past post" — the post page's entry point, resolved through its draft.

    The join is `Draft.zernio_post_id == Post.late_post_id` (`metrics.draft_for_post`), the
    same one the Inbox and the scoreboard use.
    """
    library(session)
    source = generate_draft(session, FakeLLM(WRITTEN), FakeRenderer(), idea=IDEA)
    source.zernio_post_id = "late-xyz"
    post = add_post(session, "z-retopic", 900, "what went out")
    post.late_post_id = "late-xyz"
    session.add_all([source, post])
    session.flush()
    app.dependency_overrides[get_llm] = lambda: FakeLLM(RETOPICKED)
    app.dependency_overrides[get_html_renderer] = FakeRenderer

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


def test_the_retopic_endpoint_needs_exactly_one_source(client, session):
    library(session)

    assert client.post("/drafts/retopic", json={"idea": NEW_IDEA}).status_code == 422
    assert client.post(
        "/drafts/retopic",
        json={"idea": NEW_IDEA, "source_draft_id": 1, "source_post_id": 1},
    ).status_code == 422
    assert client.post(
        "/drafts/retopic", json={"idea": NEW_IDEA, "source_draft_id": 987654}
    ).status_code == 404


def test_a_retopic_from_a_vanished_version_is_a_conflict_not_a_redraw_of_another(
    client, session
):
    """A 409 like the redraw's: the library cannot serve this draft's version, and falling
    back to a sibling is the mis-attribution the whole slice is about."""
    _, _, visual = library(session)
    source = generate_draft(session, FakeLLM(WRITTEN), FakeRenderer(), idea=IDEA)
    edited_visual(session, visual, "v2")
    session.delete(visual)
    session.flush()
    app.dependency_overrides[get_llm] = lambda: FakeLLM(RETOPICKED)
    app.dependency_overrides[get_html_renderer] = FakeRenderer

    response = client.post(
        "/drafts/retopic", json={"idea": NEW_IDEA, "source_draft_id": source.id}
    )

    assert response.status_code == 409
    assert visual.family_id in response.json()["detail"]
