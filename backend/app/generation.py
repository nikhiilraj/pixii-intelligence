from collections.abc import Mapping
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
from typing import Any

from sqlmodel import Session, col, select

from app import gates, prompts
from app.assets import resolve_asset_values
from app.config import settings
from app.llm import LLM
from app.models.draft import Draft
from app.models.post import Post
from app.models.stage import GenerationStage, advance
from app.models.template import Template, TemplateKind
from app.output_schema import validate as validate_output
from app.prompts.tracing import new_correlation_id, traced_call
from app.rendering import HtmlRenderer, ImageRenderer, render_visual
from app.templates import usable_templates

# How many of the hook's own source posts are shown as exemplars. Enough to hear the
# voice, few enough that the model imitates rather than collages.
EXEMPLAR_LIMIT = 3

# How many human rulings are carried into a prompt, most recently judged first.
# ponytail: a cap, not a summarisation pass. Ten notes is a paragraph the model can hold;
# beyond that the honest upgrade is a human editing a standing "what we learned" note, not
# this app compressing rulings it cannot weigh.
LESSON_LIMIT = 10

# Slots the model may write prose into. Anything else — an image URL, say — must come from
# a real asset: a sentence in an <img src> renders as an empty box and reports success,
# which is worse than failing.
#
# **An untyped slot is not writable.** `""` was in this set, which made every slot of every
# template authored before `type` existed writable by default — `stat-hero` v1 was APPROVED
# with four untyped slots, two of them an `<img src>`, and that is precisely how the empty-box
# draft happened. The guard cannot key on the slot's name instead: `left_image_url` reads as
# an asset but `subject`, `logo` and `hero` do not, so a name heuristic reintroduces the same
# class of silent wrongness it is meant to close. Absent means unknown, and unknown fails loud.
WRITABLE_SLOT_TYPES = {"text", "number"}


def writable_slots(visual: Template) -> list[str]:
    return [
        str(slot.get("name"))
        for slot in visual.slots
        if str(slot.get("type") or "") in WRITABLE_SLOT_TYPES
    ]


def asset_slots(visual: Template) -> list[str]:
    """The slots an asset is picked for — `image_url`, the ones the model may never write.

    The mirror image of `writable_slots`, read off the template's declared `type` for the
    same reason: `left_image_url` reads as an asset and `subject`, `logo` and `hero` do not,
    so guessing from the name reintroduces exactly the silent wrongness the type check
    closes. `.get`, never `slot["type"]` — VISUAL rows authored before `type` existed carry
    no key at all.
    """
    return [str(slot.get("name")) for slot in visual.slots if slot.get("type") == "image_url"]


def chosen_assets(visual: Template, picked: dict[str, str]) -> dict[str, str]:
    """The asset id for each of this visual's image slots: what was picked, else the default.

    Two things happen here, and both are load-bearing.

    **`picked` is filtered to image slots.** It arrives from a request body, so accepting it
    wholesale would hand a caller a second, unguarded way to write `big_number` — the exact
    hole `WRITABLE_SLOT_TYPES` exists to close, reopened one column over. A key naming
    anything but an `image_url` slot is dropped.

    **A slot may carry `default_asset_id`**, a 4th optional key in the `slots` JSONB. That is
    what lets a template complete with nobody picking: an unattended run supplies no assets at
    all, and a visual whose image slots all have defaults still renders. Stored as text
    because that is what `visual_values` already holds and what the delete guard compares —
    a slot written `{"default_asset_id": 7}` and a picker sending `"7"` must be one reference,
    not two.

    An image slot with neither a pick nor a default is left absent, so `fill()` names it in a
    `MissingSlotValue` an operator can act on rather than rendering an empty box.
    """
    values: dict[str, str] = {}
    for slot in visual.slots:
        if slot.get("type") != "image_url":
            continue
        name = str(slot.get("name"))
        default = slot.get("default_asset_id")
        ref = str(picked.get(name) or "").strip() or str(default if default is not None else "")
        if ref.strip():
            values[name] = ref.strip()
    return values


def render_values(draft: Draft) -> dict[str, str]:
    """Everything a draft's visual is filled from: the words, plus the assets over the top.

    Two columns, one dict, and only at the moment of rendering. `asset_values` wins on a
    collision because a draft generated against `stat-hero` v1 — four untyped slots, two of
    them an `<img src>` — has model prose sitting in what v2 declares an image slot, and that
    prose is the empty-box failure this slice closes.
    """
    return {**draft.visual_values, **draft.asset_values}


# The prompts this module sends, pinned to an exact version at import.
#
# **Named here, once, rather than at each `complete_json`.** The version a file sends is then
# one greppable line, and the three call sites below cannot drift apart — `regenerate_text`
# quietly sending a different version of the write prompt from `generate_draft` is the same
# class of defect as `regenerate_visual` redrawing from the newest template row.
#
# `prompts.get` takes the version because there is no "latest" to take instead; see its
# docstring. Bumping either of these is a deliberate edit to one line, which is the point.
_WRITE = prompts.get("draft.write", "1.0.0")
_EDITORIAL_WRITE = prompts.get("draft.write", "2.0.0")
_SUGGEST = prompts.get("draft.suggest_templates", "1.0.0")


class NoUsableTemplates(RuntimeError):
    """Generation needs an approved hook, structure and visual. Something is missing."""


@dataclass
class Suggestion:
    hook: Template
    structure: Template
    visual: Template
    reason: str = ""


def _approved(session: Session, kind: TemplateKind) -> list[Template]:
    templates = usable_templates(session, kind)
    if not templates:
        raise NoUsableTemplates(f"no approved {kind.value} template — approve one first")
    return templates


def _by_name(templates: list[Template], name: str | None) -> Template:
    """Resolve a cited name, falling back to the first approved template.

    A model naming something that does not exist should not cost the operator a draft.
    """
    for template in templates:
        if template.name == name:
            return template
    return templates[0]


def suggest_templates(
    session: Session,
    llm: LLM,
    idea: str,
    *,
    correlation_id: str | None = None,
    enforce_schema: bool = False,
) -> Suggestion:
    """Propose a hook, structure and visual for an idea. Every choice is overridable."""
    hooks = _approved(session, TemplateKind.HOOK)
    structures = _approved(session, TemplateKind.STRUCTURE)
    visuals = _approved(session, TemplateKind.VISUAL)

    def describe(templates: list[Template]) -> str:
        def gist(t: Template) -> str:
            return str(
                t.body.get("pattern") or t.body.get("post_type") or t.body.get("renderer", "")
            )

        return "\n".join(f"- {t.name}: {gist(t)}" for t in templates)

    chosen = traced_call(
        session,
        llm,
        _SUGGEST,
        f"Idea: {idea}\n\nHooks:\n{describe(hooks)}\n\n"
        f"Structures:\n{describe(structures)}\n\nVisuals:\n{describe(visuals)}",
        correlation_id=correlation_id or new_correlation_id(),
    )
    # The legacy generation helpers keep their tolerant fallback for historical API/tests.
    # The Studio workflow opts into strict output below, where an incomplete selection is a
    # recoverable planning failure rather than a silently different template choice.
    if enforce_schema:
        validate_output(chosen, _SUGGEST.output_schema)
    return Suggestion(
        hook=_by_name(hooks, chosen.get("hook")),
        structure=_by_name(structures, chosen.get("structure")),
        visual=_by_name(visuals, chosen.get("visual")),
        reason=str(chosen.get("reason") or ""),
    )


def variant_combinations(session: Session, count: int) -> list[tuple[Template, Template, Template]]:
    """Up to `count` distinct approved (hook, structure, visual) combinations to write one idea
    through — in the order they are produced, which is the only order this slice may have.

    **Rotated, not the cartesian product.** `product(hooks, structures, visuals)[:3]` returns
    three combinations sharing the first hook and the first structure, so the three drafts would
    be the same post with three pictures — three billed completions for one comparison. Taking
    the i-th of each list moves all three axes at once, which is what makes the drafts worth
    putting side by side at all.

    Deduplicated, so a library offering fewer combinations than asked for returns fewer variants
    rather than the same combination twice at full price: with one approved template of each kind
    there is exactly one combination, and three copies of it is three times the spend for one
    draft. `usable_templates` orders by name, so the rotation is stable across calls.

    ponytail: no diversity metric, no coverage bookkeeping, no memory of what previous batches
    used. Ceiling: something that picks combinations by what has not been tried lately — which is
    a different feature and needs a reason to prefer one axis over another.
    """
    hooks = _approved(session, TemplateKind.HOOK)
    structures = _approved(session, TemplateKind.STRUCTURE)
    visuals = _approved(session, TemplateKind.VISUAL)

    combos: dict[tuple[int | None, ...], tuple[Template, Template, Template]] = {}
    for index in range(max(count, 0)):
        combo = (
            hooks[index % len(hooks)],
            structures[index % len(structures)],
            visuals[index % len(visuals)],
        )
        combos.setdefault(tuple(t.id for t in combo), combo)
    return list(combos.values())


def _exemplars(session: Session, hook: Template) -> list[Post]:
    """The posts this hook was derived from — the voice the draft should sound like."""
    if not hook.provenance:
        return []
    statement = (
        select(Post)
        .where(col(Post.zernio_id).in_(hook.provenance))
        # The hard line. A hook may be borrowed from another creator, but this is the one
        # path where a post is handed to the model as a voice to imitate, so it is
        # restricted to Monte's own writing here rather than trusted to whatever cohort
        # the hook claims. Widening this makes Pixii's drafts sound like someone else.
        .where(Post.account_username == settings.voice_account)
        .order_by(col(Post.engaged_actions).desc())
        .limit(EXEMPLAR_LIMIT)
    )
    return list(session.exec(statement).all())


def verdict_lessons(session: Session) -> list[str]:
    """What a human ruled about individual published posts, as lines for the prompt.

    This is the whole of V2's learning, and its shape is set by what the data can carry.
    Engagement spans 12.7x across this corpus at ~3 samples per template and no post here
    records lineage attribution, so no aggregate over verdicts could rank anything. Hence:
    no averages, no counts, no win rates — each line is one ruling on one post, and the
    prompt says so.

    **A verdict with no note is skipped**, and that is load-bearing rather than tidy. The
    lesson deliberately carries only the human's own words, never the post's text, so a
    note-less ruling would reduce to the bare word `worked` attached to nothing the model
    can see — which is not advice, it is one tick in a tally, and a tally is precisely the
    statistics framing this must not introduce. The only way to make a note-less verdict
    mean anything would be to send the post's content, which is the voice-leak path
    `_exemplars` exists to keep closed. So the note is the lesson.

    Any post with a note qualifies, Monte's or a creator's, and that does not leak voice:
    a note is a human writing about a post, not a post handed over as a voice to imitate.
    `_exemplars` remains the only path where post content reaches the model.
    """
    statement = (
        select(Post)
        .where(col(Post.verdict).is_not(None))
        .where(col(Post.verdict_note) != "")
        # Most recently judged first, so the cap drops the oldest thinking rather than an
        # arbitrary row. `id` only breaks ties deterministically.
        .order_by(col(Post.verdict_at).desc(), col(Post.id).desc())
        .limit(LESSON_LIMIT)
    )
    lessons = []
    for post in session.exec(statement).all():
        note = post.verdict_note.strip()
        if note and post.verdict is not None:
            lessons.append(f'{post.verdict.value} — "{note}"')
    return lessons


# `lessons` takes no default on purpose, here and below. Three prompts now carry them, and a
# default would let any of them quietly stop passing them with nothing failing.
def lesson_lines(lessons: list[str]) -> list[str]:
    """The verdict lessons as prompt lines — and nothing at all when there are none.

    Shared with `autonomous.propose_topics` rather than restated there, so the two prompts
    frame a ruling identically: one person's judgement about one post, never a measurement.
    Restating it would let the framing drift on one side, and the framing is the whole
    guard against a verdict being read as a statistic.

    Returning `[]` for an empty list is the byte-identity guarantee both callers rely on:
    a prompt built before any verdict existed is unchanged by this code path.
    """
    if not lessons:
        return []
    return [
        "\nHuman review notes on individual published posts. Each line is one person's"
        " judgement about one post, written after it went out — not a measurement, not a"
        " count, and not evidence about the pattern in general. Weigh each as advice;"
        " they say nothing about how one template compares to another:",
        *(f"- {lesson}" for lesson in lessons),
    ]


def _write_prompt(
    idea: str,
    hook: Template,
    structure: Template,
    visual: Template,
    exemplars: list[Post],
    lessons: list[str],
) -> str:
    sections = "\n".join(
        f"{index}. {section.get('name', '')}: {section.get('guidance', '')}"
        for index, section in enumerate(structure.body.get("sections") or [], 1)
    )
    slots = ", ".join(writable_slots(visual)) or "(none)"

    parts = [
        f"Idea:\n{idea}",
        f"\nHook pattern to follow:\n{hook.body.get('pattern', '')}",
        f"Hook tone: {hook.body.get('tone', '')}",
        f"\nStructure ({structure.body.get('post_type', '')}):\n{sections}",
        f"\nVisual slots to fill: {slots}",
    ]
    # Before the exemplars, not after: the exemplar block ends with raw post bodies under a
    # `---` rule, so anything appended below it reads as commentary on the last post shown.
    # Nothing is appended at all when there are no lessons, which keeps a prompt written
    # before any verdict existed byte-identical to one written after.
    parts.extend(lesson_lines(lessons))
    if exemplars:
        parts.append("\nExemplar posts — match this voice, not this content:")
        for post in exemplars:
            parts.append(f"---\n{post.content.strip()[:1500]}")
    return "\n".join(parts)


def _written_values(written: dict, visual: Template) -> dict[str, str]:
    """Keep only values for slots the model was allowed to write.

    A model will volunteer a value for every slot it can see, including image URLs it has
    no way to know. Dropping those makes the render fail loudly instead of producing an
    image with empty boxes in it.
    """
    allowed = set(writable_slots(visual))
    supplied = written.get("visual_values") or {}
    return {k: str(v) for k, v in supplied.items() if k in allowed}


def _resolve(session: Session, kind: TemplateKind, template_id: int | None) -> Template | None:
    if template_id is None:
        return None
    template = session.get(Template, template_id)
    if template is None or template.kind is not kind:
        raise NoUsableTemplates(f"template {template_id} is not an approved {kind.value}")
    return template


def render_template(
    session: Session,
    visual: Template,
    values: dict[str, str],
    renderer: HtmlRenderer | ImageRenderer,
) -> bytes:
    """Render a visual the one way the whole application renders visuals.

    Three steps, in this order, and the order is the point:

    1. `chosen_assets` settles each `image_url` slot — what was picked, else the slot's
       `default_asset_id`.
    2. `resolve_asset_values` turns each settled reference into something loadable.
    3. `render_visual` draws it.

    The merge is `values` first and the settled assets over the top, so a caller's text and
    number slots pass through untouched. That is deliberate, not a gap: `chosen_assets`
    filters its own dict to image slots, and the merge does not extend that filter to
    `values` — nor should it, because preview exists precisely so a caller can set text and
    number slots directly. The model-written path is guarded separately and earlier, by
    `_written_values`/`writable_slots`, before a draft's values ever reach here.

    Preview used to skip step 1, so a slot with a pinned default previewed from its
    `example` and generated from the asset — two pictures from one template, with nothing
    raised. Every render path goes through here now so that cannot recur.
    """
    settled = {**values, **chosen_assets(visual, values)}
    return render_visual(visual, resolve_asset_values(session, visual, settled), renderer)


def _draw_visual(
    session: Session, draft: Draft, visual: Template, renderer: HtmlRenderer | ImageRenderer
) -> None:
    """Render the visual onto the draft. A failure records why and keeps the words.

    Asset resolution happens here rather than in either caller, and deliberately so:
    `regenerate_visual` is handed no values at all — it redraws from what the draft
    already holds — so resolving at the call sites would leave every re-render embedding
    nothing. The resolved values stay local; `draft.asset_values` keeps the asset id,
    which is what makes the next re-render resolvable too.
    """
    try:
        draft.visual_image = render_template(session, visual, render_values(draft), renderer)
        draft.visual_error = None
    except Exception as exc:  # noqa: BLE001 — any failure here must not cost the words
        draft.visual_image = None
        draft.visual_error = f"{type(exc).__name__}: {exc}"

    # `zernio_media_url` describes bytes that no longer exist here, so it cannot survive a
    # redraw. `push_draft` re-sends a stored URL to reproduce Zernio's duplicate hash, and a
    # draft that was pushed unsuccessfully, redrawn, and pushed again would otherwise create
    # the post carrying the *previous* picture — silently, with the right image on screen.
    #
    # Cleared unconditionally, including on the failure branch where the caller may put the
    # old image back: over-clearing costs one repeated upload, under-clearing publishes the
    # wrong picture. Only one of those is worth guarding against.
    draft.zernio_media_url = None


def generate_draft(
    session: Session,
    llm: LLM,
    html_renderer: HtmlRenderer,
    image_renderer: ImageRenderer,
    *,
    idea: str,
    hook_id: int | None = None,
    structure_id: int | None = None,
    visual_id: int | None = None,
    asset_values: dict[str, str] | None = None,
    mode: str = "directed",
) -> Draft:
    """Turn an idea into a reviewable draft, stamped with what produced it.

    Any template not named explicitly is suggested. Nothing here publishes; the draft is
    reviewed and only then pushed.

    `asset_values` is slot name -> asset id, for the visual's `image_url` slots. Absent keys
    fall back to the slot's `default_asset_id`, which is what lets an unattended run — which
    passes none — still render a visual carrying images.

    Both renderers, because the visual is not known until below — see `_renderer_for`.
    """
    correlation = new_correlation_id()
    hook = _resolve(session, TemplateKind.HOOK, hook_id)
    structure = _resolve(session, TemplateKind.STRUCTURE, structure_id)
    visual = _resolve(session, TemplateKind.VISUAL, visual_id)

    if hook is None or structure is None or visual is None:
        suggested = suggest_templates(session, llm, idea, correlation_id=correlation)
        hook = hook or suggested.hook
        structure = structure or suggested.structure
        visual = visual or suggested.visual

    written = traced_call(
        session,
        llm,
        _WRITE,
        _write_prompt(
            idea, hook, structure, visual, _exemplars(session, hook), verdict_lessons(session)
        ),
        correlation_id=correlation,
        input_artifact_ids={
            "hook": f"{hook.family_id}/{hook.version}",
            "structure": f"{structure.family_id}/{structure.version}",
            "visual": f"{visual.family_id}/{visual.version}",
        },
    )

    draft = Draft(
        idea=idea,
        mode=mode,
        hook_family=hook.family_id,
        hook_version=hook.version,
        structure_family=structure.family_id,
        structure_version=structure.version,
        visual_family=visual.family_id,
        visual_version=visual.version,
        hook_text=str(written.get("hook") or "").strip(),
        body_text=str(written.get("body") or "").strip(),
        visual_values=_written_values(written, visual),
        asset_values=chosen_assets(visual, asset_values or {}),
        correlation_id=correlation,
        write_prompt_name=_WRITE.name,
        write_prompt_version=_WRITE.version,
    )
    _draw_visual(session, draft, visual, _renderer_for(visual, html_renderer, image_renderer))

    session.add(draft)
    session.flush()
    return draft


def _recent_posts(session: Session, limit: int = 20) -> list[str]:
    statement = (
        select(Post.content)
        .where(Post.account_username == settings.voice_account)
        .order_by(col(Post.published_at).desc(), col(Post.id).desc())
        .limit(limit)
    )
    return [text for text in session.exec(statement).all() if text.strip()]


def _editorial_context(
    *,
    brief,
    plan,
    planned: list,
    dossier: Any | None,
    hook: Template,
    structure: Template,
    visual: Template,
    exemplars: list[Post],
    lessons: list[str],
) -> str:
    """Everything the source-disciplined write prompt is allowed to use."""
    sections = "\n".join(
        f"{index}. {section.get('name', '')}: {section.get('guidance', '')}"
        for index, section in enumerate(structure.body.get("sections") or [], 1)
    )
    parts = [
        f"Original idea:\n{brief.idea}",
        f"\nEditorial objective: {brief.objective}",
        f"Audience: {brief.audience}",
        f"Desired action: {brief.desired_action}",
        f"\nThesis: {plan.thesis}",
        f"Tension: {plan.tension}",
        f"Audience stake: {plan.audience_stake}",
        f"CTA: {plan.cta}",
        "Planned beats:\n" + "\n".join(f"- {beat}" for beat in plan.beats),
        "Planned claims:\n" + "\n".join(f"- {claim.text}" for claim in planned),
        f"\nApproved hook template: {hook.name} v{hook.version}\n{hook.body.get('pattern', '')}",
        f"Approved structure template: {structure.name} v{structure.version}\n{sections}",
        f"Approved visual template: {visual.name} v{visual.version}",
        f"Writable visual slots: {', '.join(writable_slots(visual)) or '(none)'}",
    ]
    if dossier is None:
        parts.append("\nResearch depth: none. Use no factual content beyond the original idea.")
    else:
        source_labels = {source.id: f"S{index}" for index, source in enumerate(dossier.sources, 1)}
        source_lines = [
            f"[{source_labels[source.id]}] {source.title or source.url} — {source.url}"
            for source in dossier.sources
        ]
        citation_by_id = {citation.id: citation for citation in dossier.citations}
        claim_lines = []
        for claim in dossier.claims:
            labels = []
            for citation_id in claim.supporting_citation_ids:
                citation = citation_by_id.get(citation_id)
                if citation and citation.source_id in source_labels:
                    labels.append(source_labels[citation.source_id])
            claim_lines.append(
                f"- ({claim.status}) {claim.text}"
                + (f" [{', '.join(labels)}]" if labels else " [no supporting citation]")
            )
        parts.extend(
            [
                f"\nResearch depth: {dossier.mode}. Floor: {dossier.recommended_mode}.",
                "Sources:\n" + ("\n".join(source_lines) or "(none fetched)"),
                "Dossier claims:\n" + ("\n".join(claim_lines) or "(none extracted)"),
                "Unknowns:\n" + "\n".join(f"- {item.text}" for item in dossier.unknowns),
            ]
        )
    parts.extend(lesson_lines(lessons))
    if exemplars:
        parts.append("\nMonte voice exemplars — imitate voice only, never factual content:")
        parts.extend(f"---\n{post.content.strip()[:1500]}" for post in exemplars)
    return "\n".join(parts)


def _review_dict(report: Any) -> dict[str, Any]:
    return {
        "rubric_version": report.rubric_version,
        "prompt_name": report.prompt_name,
        "prompt_version": report.prompt_version,
        "readiness_points": report.readiness_points,
        "decision": report.readiness.value,
        "summary": report.summary,
        "deductions": [asdict(item) for item in report.deductions],
    }


def _findings_dict(findings: list[gates.Finding] | tuple[gates.Finding, ...]) -> list[dict]:
    return [asdict(finding) for finding in findings]


def _research_findings(planned: list, dossier: Any | None) -> tuple[list[str], list[str]]:
    """The dossier's own verdicts, plus every planned claim it failed to settle.

    **This checks the plan, and only the plan.** A factual assertion the model invented while
    writing was never a `PlannedClaim`, so nothing here looks at it; and a `none`-mode draft
    has no dossier at all, so the early return below checks nothing whatsoever. Both holes are
    closed by `app.verification`, which reads the finished candidate instead — see its module
    docstring. The two are complementary and both feed the same gate: this one asks "did
    research settle what it was asked to", that one asks "can the post's own sentences be
    stood behind".
    """
    from app.models.research import DISPUTED, REFUTED, SUPPORTED, UNSUPPORTED

    if dossier is None:
        return [], []
    supported = [claim.text for claim in dossier.claims if claim.status == SUPPORTED]
    unsupported = [claim.text for claim in dossier.claims if claim.status in (UNSUPPORTED, REFUTED)]
    contradicted = [claim.text for claim in dossier.claims if claim.status in (DISPUTED, REFUTED)]
    # Planned claims are the contract research was asked to settle. If the dossier never
    # produced a close supported claim, writing it as fact is an evidence failure.
    for item in planned:
        backed = any(
            SequenceMatcher(None, item.text.lower(), text.lower()).ratio() >= 0.62
            for text in supported
        )
        if not backed:
            unsupported.append(item.text)
    return list(dict.fromkeys(unsupported)), list(dict.fromkeys(contradicted))


def generate_reviewed_draft(
    session: Session,
    llm: LLM,
    search: Any,
    html_renderer: HtmlRenderer,
    image_renderer: ImageRenderer,
    *,
    idea: str,
    hook_id: int | None = None,
    structure_id: int | None = None,
    visual_id: int | None = None,
    asset_values: dict[str, str] | None = None,
    requested_mode: str | None = None,
    mode: str = "directed",
    research_fetcher: Any | None = None,
) -> Draft:
    """Run the complete Studio pipeline and persist every stage on one draft.

    Both renderers, for the reason `_renderer_for` gives: the visual can be suggested here,
    and the route calling this cannot know which renderer draws it until that has happened.
    """
    from app import editorial, research, revision, rubric, verification

    correlation = new_correlation_id()
    hook = _resolve(session, TemplateKind.HOOK, hook_id)
    structure = _resolve(session, TemplateKind.STRUCTURE, structure_id)
    visual = _resolve(session, TemplateKind.VISUAL, visual_id)
    if hook is None or structure is None or visual is None:
        suggestion = suggest_templates(
            session, llm, idea, correlation_id=correlation, enforce_schema=True
        )
        hook, structure, visual = (
            hook or suggestion.hook,
            structure or suggestion.structure,
            visual or suggestion.visual,
        )

    draft = Draft(
        idea=idea,
        mode=mode,
        hook_family=hook.family_id,
        hook_version=hook.version,
        structure_family=structure.family_id,
        structure_version=structure.version,
        visual_family=visual.family_id,
        visual_version=visual.version,
        asset_values=chosen_assets(visual, asset_values or {}),
        correlation_id=correlation,
        generation_stage=GenerationStage.PLANNING,
        write_prompt_name=_EDITORIAL_WRITE.name,
        write_prompt_version=_EDITORIAL_WRITE.version,
    )
    session.add(draft)
    session.flush()

    try:
        brief = editorial.build_brief(
            session, llm, idea=idea, requested_mode=requested_mode, correlation_id=correlation
        )
        plan = editorial.plan_angle(
            session, llm, brief, recent_topics=_recent_posts(session), correlation_id=correlation
        )
        planned = editorial.planned_claims(session, plan)
        draft.editorial_brief_id = brief.id
        draft.angle_plan_id = plan.id
    except Exception as exc:  # a stored failure is recoverable from Studio
        draft.generation_stage = GenerationStage.FAILED
        draft.generation_error = f"planning: {type(exc).__name__}: {exc}"
        session.add(draft)
        session.flush()
        return draft

    found: research.ResearchDossier | None = None
    if brief.research_mode != research.NONE:
        draft.generation_stage = GenerationStage.RESEARCHING
        session.flush()
        try:
            research_args: dict[str, Any] = {
                "question": editorial.research_question(idea, [item.text for item in planned]),
                "mode": brief.research_mode,
                "correlation_id": correlation,
            }
            if research_fetcher is not None:
                research_args["fetcher"] = research_fetcher
            found = research.run_research(session, llm, search, **research_args)
            draft.research_job_id = found.job_id
        except Exception as exc:
            job = session.exec(
                select(research.ResearchJob)
                .where(research.ResearchJob.correlation_id == correlation)
                .order_by(col(research.ResearchJob.id).desc())
            ).first()
            draft.research_job_id = job.id if job else None
            draft.generation_stage = GenerationStage.FAILED
            draft.generation_error = f"researching: {type(exc).__name__}: {exc}"
            session.add(draft)
            session.flush()
            return draft

    draft.generation_stage = GenerationStage.DRAFTING
    message = _editorial_context(
        brief=brief,
        plan=plan,
        planned=planned,
        dossier=found,
        hook=hook,
        structure=structure,
        visual=visual,
        exemplars=_exemplars(session, hook),
        lessons=verdict_lessons(session),
    )
    try:
        written: Mapping[str, Any] = traced_call(
            session,
            llm,
            _EDITORIAL_WRITE,
            message,
            correlation_id=correlation,
            input_artifact_ids={
                "draft": draft.id,
                "editorial_brief": brief.id,
                "angle_plan": plan.id,
                "research_job": found.job_id if found else None,
                "hook": f"{hook.family_id}/{hook.version}",
                "structure": f"{structure.family_id}/{structure.version}",
                "visual": f"{visual.family_id}/{visual.version}",
            },
        )
        validate_output(written, _EDITORIAL_WRITE.output_schema)
    except Exception as exc:
        draft.generation_stage = GenerationStage.FAILED
        draft.generation_error = f"drafting: {type(exc).__name__}: {exc}"
        session.add(draft)
        session.flush()
        return draft

    recent = _recent_posts(session)
    unsupported, contradicted = _research_findings(planned, found)
    findings = gates.check(
        written,
        template=visual,
        recent_posts=recent,
        asset_values=draft.asset_values,
        unsupported_claims=unsupported,
        contradicted_claims=contradicted,
    )
    evidence_failure = any(
        finding.gate in {"uncited_claim", "contradicted_claim"} for finding in findings
    )
    if findings and not evidence_failure:
        try:
            revised = revision.revise(
                written,
                template=visual,
                recent_posts=recent,
                idea=idea,
                llm=llm,
                session=session,
                correlation_id=correlation,
                asset_values=draft.asset_values,
                unsupported_claims=unsupported,
                contradicted_claims=contradicted,
            )
            written = revised.candidate
            findings = list(revised.findings)
            draft.revision_rounds = revised.rounds
        except Exception as exc:
            draft.generation_error = f"revision: {type(exc).__name__}: {exc}"

    draft.hook_text = str(written.get("hook") or "").strip()
    draft.body_text = str(written.get("body") or "").strip()
    draft.visual_values = _written_values(dict(written), visual)

    # Whether verification ran and could not be read. Tracked separately from `findings`
    # because a verifier that answered unusably has not found a bad claim — it has failed to
    # look — and the two must not arrive on the review screen as the same thing.
    unverified = False
    if not findings:
        # **Only for a candidate that is otherwise clean.** One already carrying findings is
        # going to `failed_review` whatever this says, and the call is billed; the rubric is
        # skipped on exactly the same condition below, for exactly the same reason.
        #
        # `advance`, not an assignment, and **nothing here may set `REVISING`**: `ORDER` puts
        # `VERIFYING` before it, so a run that recorded the revision loop as a stage could
        # never legally reach this one. The loop above deliberately leaves the stage alone.
        draft.generation_stage = advance(draft.generation_stage, GenerationStage.VERIFYING)
        session.flush()
        try:
            review = verification.verify(
                session,
                llm,
                candidate=written,
                idea=idea,
                dossier=found,
                correlation_id=correlation,
            )
        except Exception as exc:
            # Fail closed. A verifier nobody could read has not cleared this draft, and a
            # draft that reached `ready` on a broken verifier is an evidence gate switched
            # off silently — the failure this whole stage exists to make impossible.
            unverified = True
            draft.generation_stage = GenerationStage.FAILED_REVIEW
            draft.generation_error = f"verifying: {type(exc).__name__}: {exc}"
        else:
            draft.verification_result = review.as_dict()
            # **The union, not the verification lists alone.** Reaching here does not mean
            # the research lists were empty: a planned claim the dossier failed to settle is
            # in `unsupported` whether or not the post's wording echoes it closely enough to
            # have raised a finding the first time. Passing only the new lists would drop
            # every one of those, so the check that already worked would stop working the day
            # this one was added.
            findings = gates.check(
                written,
                template=visual,
                recent_posts=recent,
                asset_values=draft.asset_values,
                unsupported_claims=[*unsupported, *review.unsupported],
                contradicted_claims=[*contradicted, *review.contradicted],
            )

    draft.gate_results = _findings_dict(findings)

    # `unverified` already recorded its own stage and reason; nothing below may overwrite it,
    # least of all the rubric, which would be scoring wording nobody could check the facts of.
    if not unverified and findings:
        draft.generation_stage = GenerationStage.FAILED_REVIEW
        draft.generation_error = draft.generation_error or "deterministic quality gates failed"
    elif not unverified:
        draft.generation_stage = GenerationStage.EVALUATING
        try:
            report = rubric.evaluate(
                written,
                template=visual,
                recent_posts=recent,
                idea=idea,
                llm=llm,
                session=session,
                correlation_id=correlation,
                asset_values=draft.asset_values,
            )
            draft.readiness_result = _review_dict(report)
            draft.generation_stage = (
                GenerationStage.READY
                if report.readiness is rubric.Readiness.READY_FOR_EDITORIAL_REVIEW
                else GenerationStage.FAILED_REVIEW
            )
            if draft.generation_stage == GenerationStage.FAILED_REVIEW:
                draft.generation_error = "editorial-readiness evaluation requires revision"
        except Exception as exc:
            draft.generation_stage = GenerationStage.FAILED_REVIEW
            draft.generation_error = f"evaluating: {type(exc).__name__}: {exc}"

    _draw_visual(session, draft, visual, _renderer_for(visual, html_renderer, image_renderer))
    session.add(draft)
    session.flush()
    return draft


def regenerate_text(session: Session, llm: LLM, draft: Draft) -> Draft:
    """Rewrite the words against the same templates. Lineage does not move.

    `generated_from`, not the newest row in each family: that sentence has to be true of the
    templates actually used, not merely of the columns left unchanged. Resolving by family
    alone rewrote the words against an edited hook while `hook_version` still named the old
    one, so `lineage_metadata` pushed the old version to Zernio and `template_performance`
    credited it with text a different template wrote. The same defect `regenerate_visual` had,
    on three families instead of one — and it reached `_written_values` below too, since the
    newest visual's slots are not necessarily this draft's.
    """
    hook = generated_from(session, draft.hook_family, draft.hook_version)
    structure = generated_from(session, draft.structure_family, draft.structure_version)
    visual = generated_from(session, draft.visual_family, draft.visual_version)

    correlation = draft.correlation_id or new_correlation_id()
    written = traced_call(
        session,
        llm,
        _WRITE,
        # Lessons are fetched here too, not only in `generate_draft`. Passing them at one
        # call site would make every rewrite silently drop them — the draft would improve
        # once and un-improve the moment anyone pressed regenerate.
        _write_prompt(
            draft.idea, hook, structure, visual, _exemplars(session, hook), verdict_lessons(session)
        ),
        correlation_id=correlation,
        input_artifact_ids={
            "draft": draft.id,
            "hook": f"{hook.family_id}/{hook.version}",
            "structure": f"{structure.family_id}/{structure.version}",
            "visual": f"{visual.family_id}/{visual.version}",
        },
    )
    draft.correlation_id = correlation
    draft.write_prompt_name = _WRITE.name
    draft.write_prompt_version = _WRITE.version
    draft.hook_text = str(written.get("hook") or "").strip()
    draft.body_text = str(written.get("body") or "").strip()
    draft.visual_values = _written_values(written, visual)
    # `asset_values` is untouched. Rewriting the words is not a reason to discard the assets
    # someone picked for the picture, and the model has no say in them either way.
    session.add(draft)
    session.flush()
    return draft


def generated_from(session: Session, family: str | None, version: int | None) -> Template:
    """The exact version a draft was generated from — never whatever is newest now.

    Keyed on `(family_id, version)`, like `assets._image_slot_names`, and for its reason:
    editing a template writes a new row, so the latest version of the family is a different
    template than the one the draft's lineage names. Redrawing from the latest stored a
    picture v3 produced while `visual_version` still said 2 — `publishing.lineage_metadata`
    then pushed `visual_version: 2` to Zernio and `metrics.template_performance` credited v2
    with v3's work. Nothing raised; the draft looked fine.

    A RETIRED recorded version still redraws. A redraw is a re-render of what this draft
    already is, not a new generation, so the honest picture is the one its lineage claims;
    `usable_templates` is what keeps retired versions out of everything that *chooses* a
    template. A version that is not there at all, or a draft that records none, cannot be
    redrawn faithfully and says so.
    """
    if version is None:
        raise NoUsableTemplates(
            f"draft records no version for template family {family} — cannot redraw"
        )
    template = session.exec(
        select(Template).where(Template.family_id == family, Template.version == version)
    ).first()
    if template is None:
        raise NoUsableTemplates(f"template family {family} v{version} no longer exists")
    return template


def _renderer_for(
    visual: Template, html_renderer: HtmlRenderer, image_renderer: ImageRenderer
) -> HtmlRenderer | ImageRenderer:
    """The renderer a *settled* visual declares. Beside `generated_from` because it is the
    same rule: read it off the exact row that will be used, never off a stand-in.

    This lived at the route, which is where it was wrong — not in what it computed but in
    when. `POST /drafts/workflow` resolved it from `payload.visual_id`, so a request naming
    no visual resolved `None` to the HTML renderer and *then* let `suggest_templates` pick an
    `ai` template. `render_visual` raised `UnsupportedRenderer`, `_draw_visual` swallowed it
    into `visual_error`, and the draft arrived looking generated with no picture. Every model
    suggestion was exposed to this; the autonomous run, injected an HTML renderer and naming
    no templates at all, could never draw an AI visual.

    `api_drafts._renderer` answers a neighbouring question and deliberately stays there:
    it resolves from a *draft's recorded* `(family, version)` for a redraw, where the row is
    history rather than a choice. One helper serving both would be wrong for one of them —
    a redraw must not consult a template nobody chose, and generation has no draft to read.

    Two renderers rather than a resolver callable, and not only for the shorter diff: a
    callable is a seam a caller could fill with a lookup by "latest", which is precisely the
    mistake `generated_from` above exists to prevent. Two concrete parameters also keep each
    adapter's `SpendMeter.watch` visible at the call site, where the billing is.
    """
    return image_renderer if visual.body.get("renderer") == "ai" else html_renderer


def regenerate_visual(
    session: Session, draft: Draft, renderer: HtmlRenderer | ImageRenderer
) -> Draft:
    """Redraw the image from the values already written. The words are untouched."""
    visual = generated_from(session, draft.visual_family, draft.visual_version)
    _draw_visual(session, draft, visual, renderer)
    session.add(draft)
    session.flush()
    return draft


def retopic(
    session: Session,
    llm: LLM,
    search: Any,
    html_renderer: HtmlRenderer,
    image_renderer: ImageRenderer,
    source: Draft,
    *,
    idea: str,
) -> Draft:
    """A new draft on a new subject, written through the source's exact templates.

    The pillar this app was asked for: "have the template and hooks and visuals as kind of
    template so that we can easily recreate them using another topic." Not a rewrite —
    `regenerate_text` is that, and it deliberately holds one draft's lineage still. This
    writes a **new row** whose three `(family, version)` pairs are the source's own.

    **Resolved through `generated_from`, never the newest version of the family**, and that
    is the whole of the slice. Passing `family` alone — or reading `usable_templates` — would
    write the new draft with v3 while stamping it v1, which is the G2 defect
    (`regenerate_visual` storing a v3 picture under `visual_version: 2`) reproduced in a
    route whose entire purpose is to carry a template forward faithfully. The version history
    is the attribution record, so a retired or superseded version re-topics exactly like a
    live one: nothing is being *chosen* here, it is being inherited.

    **The templates are inherited; the editorial work is not.** `generate_reviewed_draft` does
    the rest, and it is the reviewed workflow rather than `generate_draft` deliberately: a new
    subject is a new brief, a new angle, new planned claims and its own research floor. Reusing
    the source's would be re-topicking the *reasoning* as well as the shape, which is not what
    this route promises and would attach a dossier gathered for one subject to another. It also
    means what comes back is `unreviewed` only if nothing reviewed it — a re-topic is not
    pushable merely because a model returned text against a template that once worked.

    It is handed ids of the resolved rows rather than families, so `_resolve` reads back the
    same versions, and both renderers pass through for `_renderer_for` to choose between off
    `visual` below. That is the same row this function resolved, so the renderer follows the
    inherited version too.

    ponytail: the source's `asset_values` carry over as the picks. The visual is the *same
    template row*, so `chosen_assets`' image-slot filter is a no-op here rather than a silent
    drop, and a re-topic that rendered a `MissingSlotValue` where its source rendered a
    picture would be useless. Ceiling: choosing different assets for the new subject is the
    picker's job (US-015), on the draft this returns.
    """
    hook = generated_from(session, source.hook_family, source.hook_version)
    structure = generated_from(session, source.structure_family, source.structure_version)
    visual = generated_from(session, source.visual_family, source.visual_version)
    return generate_reviewed_draft(
        session,
        llm,
        search,
        html_renderer,
        image_renderer,
        idea=idea,
        hook_id=hook.id,
        structure_id=structure.id,
        visual_id=visual.id,
        asset_values=source.asset_values,
        # No `requested_mode`. The floor is detected from the new idea by `resolve_mode`, which
        # is the point: a re-topic of an opinion piece onto a factual subject needs research
        # the source never did, and inheriting the source's mode would write the new claims
        # from nothing.
        #
        # `mode` stays "directed": a human supplied this idea. A third value would be a new
        # partition of every query that reads the column, for no reader.
    )
