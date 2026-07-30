from dataclasses import dataclass

from sqlmodel import Session, col, select

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

_WRITE_SYSTEM = """\
You write LinkedIn posts in an established voice, following a given hook pattern and post
structure.

Rules:
- Follow the hook pattern's shape. Do not copy its example wording.
- Follow the structure's sections in order. Each section's guidance is a requirement.
- Match the voice of the exemplar posts: their casing, rhythm, sentence length and how they
  handle numbers. Imitate the manner, never the content.
- Use only facts present in the idea. Invent no statistics, names, or outcomes.
- No hashtags. No emoji. No "in today's fast-paced world" openings.
- Fill every visual slot you are given with a short value drawn from the post.

Return ONLY JSON of this shape, with no commentary:
{
  "hook": "the opening line or two",
  "body": "the rest of the post, blank line between paragraphs",
  "visual_values": {"slot_name": "short value"}
}"""

_SUGGEST_SYSTEM = """\
You choose which templates suit an idea.

Pick exactly one hook, one structure and one visual from the supplied lists, by name.
Choose on fit between the idea and what each template is for. Return ONLY JSON:
{"hook": "name", "structure": "name", "visual": "name", "reason": "one sentence"}"""


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
        _SUGGEST_SYSTEM,
        f"Idea: {idea}\n\nHooks:\n{describe(hooks)}\n\n"
        f"Structures:\n{describe(structures)}\n\nVisuals:\n{describe(visuals)}",
    )
    return Suggestion(
        hook=_by_name(hooks, chosen.get("hook")),
        structure=_by_name(structures, chosen.get("structure")),
        visual=_by_name(visuals, chosen.get("visual")),
        reason=str(chosen.get("reason") or ""),
    )


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


def _write_prompt(
    idea: str, hook: Template, structure: Template, visual: Template, exemplars: list[Post]
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


def _draw_visual(
    session: Session, draft: Draft, visual: Template, renderer: HtmlRenderer | ImageRenderer
) -> None:
    """Render the visual onto the draft. A failure records why and keeps the words.

    Asset resolution happens here rather than in either caller, and deliberately so:
    `regenerate_visual` is handed no values at all — it redraws from what the draft
    already holds — so resolving at the call sites would leave every re-render embedding
    nothing. The resolved values stay local; `draft.visual_values` keeps the asset id,
    which is what makes the next re-render resolvable too.
    """
    try:
        values = resolve_asset_values(session, visual, draft.visual_values)
        draft.visual_image = render_visual(visual, values, renderer)
        draft.visual_error = None
    except Exception as exc:  # noqa: BLE001 — any failure here must not cost the words
        draft.visual_image = None
        draft.visual_error = f"{type(exc).__name__}: {exc}"


def generate_draft(
    session: Session,
    llm: LLM,
    renderer: HtmlRenderer | ImageRenderer,
    *,
    idea: str,
    hook_id: int | None = None,
    structure_id: int | None = None,
    visual_id: int | None = None,
    mode: str = "directed",
) -> Draft:
    """Turn an idea into a reviewable draft, stamped with what produced it.

    Any template not named explicitly is suggested. Nothing here publishes; the draft is
    reviewed and only then pushed.
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
        _WRITE_SYSTEM,
        _write_prompt(idea, hook, structure, visual, _exemplars(session, hook)),
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
    )
    _draw_visual(session, draft, visual, renderer)

    session.add(draft)
    session.flush()
    return draft


def _current(session: Session, family: str | None) -> Template:
    """The version of a family this draft was generated from."""
    statement = (
        select(Template)
        .where(Template.family_id == family)
        .order_by(col(Template.version).desc())
    )
    template = session.exec(statement).first()
    if template is None:
        raise NoUsableTemplates(f"template family {family} no longer exists")
    return template


def regenerate_text(session: Session, llm: LLM, draft: Draft) -> Draft:
    """Rewrite the words against the same templates. Lineage does not move."""
    hook = _current(session, draft.hook_family)
    structure = _current(session, draft.structure_family)
    visual = _current(session, draft.visual_family)

    written = llm.complete_json(
        _WRITE_SYSTEM,
        _write_prompt(draft.idea, hook, structure, visual, _exemplars(session, hook)),
    )
    draft.hook_text = str(written.get("hook") or "").strip()
    draft.body_text = str(written.get("body") or "").strip()
    draft.visual_values = _written_values(written, visual)
    session.add(draft)
    session.flush()
    return draft


def regenerate_visual(
    session: Session, draft: Draft, renderer: HtmlRenderer | ImageRenderer
) -> Draft:
    """Redraw the image from the values already written. The words are untouched."""
    _draw_visual(session, draft, _current(session, draft.visual_family), renderer)
    session.add(draft)
    session.flush()
    return draft
