from dataclasses import dataclass

from sqlmodel import Session, col, select

from app import prompts
from app.assets import resolve_asset_values
from app.config import settings
from app.llm import LLM
from app.models.draft import Draft
from app.models.post import Post
from app.models.template import Template, TemplateKind
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


def suggest_templates(session: Session, llm: LLM, idea: str) -> Suggestion:
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

    chosen = llm.complete_json(
        _SUGGEST.text,
        f"Idea: {idea}\n\nHooks:\n{describe(hooks)}\n\n"
        f"Structures:\n{describe(structures)}\n\nVisuals:\n{describe(visuals)}",
    )
    return Suggestion(
        hook=_by_name(hooks, chosen.get("hook")),
        structure=_by_name(structures, chosen.get("structure")),
        visual=_by_name(visuals, chosen.get("visual")),
        reason=str(chosen.get("reason") or ""),
    )


def variant_combinations(
    session: Session, count: int
) -> list[tuple[Template, Template, Template]]:
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
    renderer: HtmlRenderer | ImageRenderer,
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
    """
    hook = _resolve(session, TemplateKind.HOOK, hook_id)
    structure = _resolve(session, TemplateKind.STRUCTURE, structure_id)
    visual = _resolve(session, TemplateKind.VISUAL, visual_id)

    if hook is None or structure is None or visual is None:
        suggested = suggest_templates(session, llm, idea)
        hook = hook or suggested.hook
        structure = structure or suggested.structure
        visual = visual or suggested.visual

    written = llm.complete_json(
        _WRITE.text,
        _write_prompt(
            idea, hook, structure, visual, _exemplars(session, hook), verdict_lessons(session)
        ),
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
    )
    _draw_visual(session, draft, visual, renderer)

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

    written = llm.complete_json(
        _WRITE.text,
        # Lessons are fetched here too, not only in `generate_draft`. Passing them at one
        # call site would make every rewrite silently drop them — the draft would improve
        # once and un-improve the moment anyone pressed regenerate.
        _write_prompt(
            draft.idea, hook, structure, visual, _exemplars(session, hook), verdict_lessons(session)
        ),
    )
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
    renderer: HtmlRenderer | ImageRenderer,
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

    `generate_draft` does the rest — it already takes the three templates as parameters, so
    there is no second generation path to keep in step with this one. It is handed ids of the
    resolved rows rather than families, so `_resolve` reads back the same versions.

    ponytail: the source's `asset_values` carry over as the picks. The visual is the *same
    template row*, so `chosen_assets`' image-slot filter is a no-op here rather than a silent
    drop, and a re-topic that rendered a `MissingSlotValue` where its source rendered a
    picture would be useless. Ceiling: choosing different assets for the new subject is the
    picker's job (US-015), on the draft this returns.
    """
    hook = generated_from(session, source.hook_family, source.hook_version)
    structure = generated_from(session, source.structure_family, source.structure_version)
    visual = generated_from(session, source.visual_family, source.visual_version)
    return generate_draft(
        session,
        llm,
        renderer,
        idea=idea,
        hook_id=hook.id,
        structure_id=structure.id,
        visual_id=visual.id,
        asset_values=source.asset_values,
        # `mode` stays "directed": a human supplied this idea. A third value would be a new
        # partition of every query that reads the column, for no reader.
    )
