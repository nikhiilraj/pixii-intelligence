import io
import logging
from enum import StrEnum

from PIL import Image
from sqlalchemy import func
from sqlmodel import Session, col, select

from app.config import settings
from app.llm import LLM
from app.models.post import Post
from app.models.template import Template, TemplateKind
from app.rendering import DEFAULT_HEIGHT, DEFAULT_WIDTH, SLOT
from app.templates import create_template, usable_templates

log = logging.getLogger(__name__)

# How many of the strongest posts the model is shown. Enough to see a pattern repeat,
# few enough that the weak tail cannot dilute it.
DEFAULT_SAMPLE_SIZE = 12

# Fewer than a hook sample: every entry here is a full image in the request, and a layout
# repeats visibly across far fewer examples than a sentence pattern does.
VISUAL_SAMPLE_SIZE = 5

# Suffixes `media.download_post_media` stores that are still images. A video frame is not
# the artefact — the post's picture is — so `.mp4`, `.mov` and `.webm` are not here.
_STILL_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".webp")

# The opening is the hook. Sending whole posts buries it and wastes context.
HOOK_CHARS = 220

_SYSTEM = """\
You extract reusable hook patterns from social posts that already performed well.

A hook is the opening — the first line or two that decides whether someone keeps reading.
Your job is to find the *repeatable shape* underneath specific wording, so it can be reused
for different subject matter.

Rules:
- Abstract only what genuinely repeats or is genuinely transferable. Do not invent patterns.
- Express each pattern with {slot_name} placeholders for the parts that change.
- Ground every pattern in the posts that justify it, by their id. Never cite an id you were
  not given.
- Describe tone concretely (casing, rhythm, whether it leads with a number), not as praise.
- Prefer fewer, sharper patterns over many overlapping ones.

Return ONLY JSON of this shape, with no commentary:
{
  "hooks": [
    {
      "name": "short-kebab-name",
      "pattern": "{slot} literal text {slot}",
      "tone": "concrete description",
      "slots": [{"name": "slot", "example": "a real example"}],
      "source_post_ids": ["id", "..."],
      "rationale": "why this works, one sentence"
    }
  ]
}"""


_STRUCTURE_SYSTEM = """\
You extract reusable post structures from social posts that already performed well.

A structure is the ordered shape of a whole post — what the beginning, middle and end each
do — independent of the subject matter. It is what gives a draft a shape to follow.

Rules:
- Give each section a short name and concrete guidance on what belongs there. Guidance must
  be actionable ("state the cost in dollars"), never vague ("be engaging").
- Sections are ordered: beginning first, end last.
- Base every structure on what the strongest posts actually do. Do not invent a shape.
- Name the post type you are describing, e.g. "offer-reward" (a post that gives something
  away in exchange for a comment or follow) or "deep-research" (a post presenting original
  findings or a teardown).
- In "compatible_hooks", name only hooks from the supplied list of available hook templates.
  If none fit, return an empty list.
- Prefer two or three sharply different structures over many similar ones.

Return ONLY JSON of this shape, with no commentary:
{
  "structures": [
    {
      "name": "short-kebab-name",
      "post_type": "offer-reward",
      "sections": [{"name": "hook", "guidance": "what this section must do"}],
      "compatible_hooks": ["hook-template-name"],
      "rationale": "one sentence"
    }
  ]
}"""

# Whole posts, since a structure is about the shape of the entire thing. The corpus
# averages ~818 characters, so this rarely truncates.
POST_CHARS = 2000


class Cohort(StrEnum):
    """Which body of work a sample is drawn from.

    A hook and a structure are borrowable shapes, so both cohorts may teach them. A voice
    is not borrowable: only VOICE may ever reach generation as a tone reference, and
    `generation._exemplars` enforces that independently of what a template claims.
    """

    VOICE = "voice"
    INSPIRATION = "inspiration"


class ExtractionError(RuntimeError):
    """The model's proposal could not be turned into templates."""


def _hook_of(content: str) -> str:
    opening = " / ".join(line.strip() for line in content.splitlines() if line.strip())
    return opening[:HOOK_CHARS]


def _strongest_posts(
    session: Session,
    platform: str,
    sample_size: int,
    cohort: Cohort,
    require_content: bool = True,
) -> list[Post]:
    """The sample the model learns from.

    Every exclusion is in the query, before the limit, so a post that cannot be evidence
    never costs a real post its slot.
    """
    account = settings.voice_account if cohort is Cohort.VOICE else settings.inspiration_account
    statement = (
        select(Post)
        .where(Post.platform == platform)
        # One cohort at a time, never mixed: a sample led by whichever cohort happened to
        # rank higher would describe neither.
        .where(Post.account_username == account)
        # Held out by hand — strong for a reason that cannot repeat.
        .where(col(Post.excluded_from_extraction).is_(False))
    )
    if require_content:
        # A post with no text carries no hook, so it is no evidence. Load-bearing in both
        # cohorts: at least one creator post is an image with no text at all.
        #
        # Visual extraction passes False. That same textless post is a picture that
        # performed, which is exactly the evidence a layout is drawn from — filtering it
        # here would discard the purest sample in the set.
        statement = statement.where(func.trim(col(Post.content)) != "")
    if cohort is Cohort.VOICE:
        # An era floor on Monte's own history: his older posts are a different genre and
        # their engagement is not comparable, having reached a different audience. It has
        # no meaning for inspiration — those posts are never ranked against Monte's, and
        # the date Monte's current voice began says nothing about someone else's timeline.
        # Gating them on it would silently discard borrowable shapes for no reason.
        # A NULL published_at fails this comparison and is excluded too: an undated post
        # cannot be shown to be current.
        statement = statement.where(col(Post.published_at) >= settings.voice_since)
    statement = statement.order_by(col(Post.engaged_actions).desc()).limit(sample_size)
    return list(session.exec(statement).all())


def _visual_sample(
    session: Session, platform: str, sample_size: int, cohort: Cohort
) -> list[tuple[Post, bytes]]:
    """The strongest posts that have a still image, paired with the image's bytes.

    Returns pairs rather than posts because the caller needs the bytes twice — once to
    send to the model, once to take the template's dimensions from. Re-resolving a file
    from `Template.provenance` later is not possible in one step: that column is
    `list[str]` of Zernio ids, so it would mean a `Post` lookup for bytes already in hand.

    An unreadable file is skipped, not raised. `media/` is a cache of Zernio's files; one
    truncated download must not cost the other four posts their slot.
    """
    pairs: list[tuple[Post, bytes]] = []
    # Over-fetch, because the media filter cannot be expressed in the ranking query without
    # assuming a suffix convention the column does not guarantee.
    for post in _strongest_posts(
        session, platform, sample_size * 4, cohort, require_content=False
    ):
        name = post.local_media_path or ""
        if not name.lower().endswith(_STILL_SUFFIXES):
            continue
        try:
            pairs.append((post, (settings.media_dir / name).read_bytes()))
        except OSError:
            continue
        if len(pairs) == sample_size:
            break
    return pairs


# A transcription of monte-workshop/bots-and-tools/brand/brand.json's colors, fonts and
# quick_rules (version 2026-06-10), rather than read across repositories at runtime.
# ponytail: one constant, re-copied when the brand changes. Make it a fetch when a second
# tool in this repo needs the same values.
BRAND = """\
Colours: #d65831 primary orange is the ONLY saturated colour — everything else is neutral.
#FAFAF8 page background (warm smoke, not cold white). #FFFFFF card surface. #1A1816 text.
#7A756D muted text. #E0DFDB borders. On an orange background use white text, with
rgba(0,0,0,0.1-0.15) for any card overlay.
Type: Cabinet Grotesk Bold or Extrabold for headlines, set large and tight. No full stops
in headlines. Use digits, never spelled-out numbers.
Layout: rounded corners — 12-20px on cards, 8-10px on buttons. No heavy drop shadows.
Clean whitespace. No eyebrow or kicker tag above a headline.
The logo is never typed as text in any font. It arrives as a real file through an image
slot."""

_VISUAL_SYSTEM = f"""\
You extract reusable visual layouts from the images of social posts that already
performed well.

You are given those images, strongest first. Your job is to express the *repeatable
layout* underneath the specific subject matter as HTML, so it can be reused for a
different subject next week.

The output is rendered by a headless browser at the size of the source image. It is not
sent to an image model, so every word and number in it is exact.

Rules:
- Return complete, self-contained HTML: one root element with an inline <style>. No
  external stylesheets, no <img> src you invent, no JavaScript, no web fonts.
- Put a {{slot_name}} placeholder wherever the content changes between posts. Every
  placeholder in the markup must appear in "slots", and every slot must appear in the
  markup. This is checked, and a mismatch discards the proposal.
- Slot "type" is "text" for words and numbers, "image_url" for a picture. An "image_url"
  slot renders as <img src="{{slot}}">.
- If the source image carries a brand mark, give that slot "role": "logo". It is filled
  from a real logo file, never drawn.
- Do not reproduce the source's words. The example values are illustrations of the shape.
- Abstract only what genuinely repeats. Do not invent a layout no image shows.
- Ground every layout in the images that justify it, by their id. Never cite an id you
  were not given.
- Prefer two or three sharply different layouts over many similar ones.
- The brand rules below outrank the source image. Some posts that performed well break
  them — a kicker above the headline, a full stop at the end of one. Do not carry that
  across: follow the rule, and name the rule the source broke in the rationale.
  brand.json is where the brand is decided; the corpus is only where shapes are found.

Brand:
{BRAND}

Return ONLY JSON of this shape, with no commentary:
{{
  "visuals": [
    {{
      "name": "short-kebab-name",
      "html": "<div style=…>{{kicker}}</div><h1>{{headline}}</h1>",
      "slots": [
        {{"name": "kicker", "type": "text", "example": "a real example"}},
        {{"name": "logo", "type": "image_url", "role": "logo", "example": "https://…"}}
      ],
      "source_post_ids": ["id"],
      "rationale": "why this shape works, one sentence"
    }}
  ]
}}"""


class _RejectedProposal(RuntimeError):
    """One proposal is unusable. The others in the batch are not."""


def _to_visual(
    session: Session, proposal: dict, sizes: dict[str, tuple[int, int]], cohort: Cohort
) -> Template:
    if not isinstance(proposal, dict):
        raise _RejectedProposal(f"proposal is not an object: {proposal!r}")

    name = (proposal.get("name") or "").strip()
    markup = (proposal.get("html") or "").strip()
    if not name or not markup:
        raise _RejectedProposal(f"proposal missing name or html: {proposal!r}")

    slots = proposal.get("slots") or []
    if not isinstance(slots, list) or any(not isinstance(slot, dict) for slot in slots):
        raise _RejectedProposal(f"{name}: slots must be a list of objects, got {slots!r}")
    declared = {str(slot.get("name")) for slot in slots}
    used = set(SLOT.findall(markup))
    if used != declared:
        # Both directions matter and they fail differently. A placeholder with no slot
        # raises MissingSlotValue in Studio, after a human approved it. A slot with no
        # placeholder is a control the picker offers that changes nothing on the image.
        raise _RejectedProposal(
            f"{name}: markup and slots disagree — "
            f"undeclared {sorted(used - declared)}, unused {sorted(declared - used)}"
        )

    # Keep only ids the model was actually shown — provenance has to be checkable.
    provenance = [pid for pid in proposal.get("source_post_ids") or [] if pid in sizes]
    # The size is the source's, and only when there is exactly one source to take it from.
    # Averaging two sizes would invent a third that no post ever used.
    width, height = sizes[provenance[0]] if len(provenance) == 1 else (
        DEFAULT_WIDTH,
        DEFAULT_HEIGHT,
    )

    return create_template(
        session,
        kind=TemplateKind.VISUAL,
        name=name,
        body={
            "renderer": "html",
            "html": markup,
            "width": width,
            "height": height,
            "rationale": (proposal.get("rationale") or "").strip(),
            "cohort": cohort.value,
        },
        slots=slots,
        provenance=provenance,
    )


def _size_of(raw: bytes) -> tuple[int, int]:
    """The source image's dimensions, which become the template's."""
    return Image.open(io.BytesIO(raw)).size


def _visual_prompt(sample: list[tuple[Post, bytes]]) -> str:
    lines = [
        "Post images, strongest first. 'engaged' is likes + comments + shares + saves — "
        "the measure that matters. Weight the top of this list most heavily.",
        "",
    ]
    for post, _ in sample:
        lines.append(f"id: {post.zernio_id} | engaged: {post.engaged_actions}")
    return "\n".join(lines)


def propose_visuals(
    session: Session,
    llm: LLM,
    *,
    platform: str = "linkedin",
    sample_size: int = VISUAL_SAMPLE_SIZE,
    cohort: Cohort = Cohort.VOICE,
) -> list[Template]:
    """Propose visual layouts from the images of the strongest posts. Proposals only."""
    cohort = Cohort(cohort)
    sample = _visual_sample(session, platform, sample_size, cohort)
    if not sample:
        return []

    result = llm.complete_json(
        _VISUAL_SYSTEM, _visual_prompt(sample), [raw for _, raw in sample]
    )
    proposals = result.get("visuals")
    if not isinstance(proposals, list):
        raise ExtractionError(f"expected a 'visuals' list, got keys {sorted(result)}")

    sizes = {post.zernio_id: _size_of(raw) for post, raw in sample}
    kept: list[Template] = []
    for proposal in proposals:
        try:
            kept.append(_to_visual(session, proposal, sizes, cohort))
        except _RejectedProposal as exc:
            # One bad layout in five must not cost the other four. The reason is logged
            # rather than raised, and the proposal simply never appears.
            log.warning("visual proposal rejected: %s", exc)
    return kept


def _build_prompt(posts: list[Post]) -> str:
    lines = [
        "Posts, strongest first. 'engaged' is likes + comments + shares + saves — the "
        "measure that matters. Weight the top of this list most heavily.",
        "",
    ]
    for post in posts:
        lines.append(f"id: {post.zernio_id} | engaged: {post.engaged_actions}")
        lines.append(f"hook: {_hook_of(post.content)}")
        lines.append("")
    return "\n".join(lines)


def _to_template(
    session: Session, proposal: dict, known_ids: set[str], cohort: Cohort
) -> Template:
    name = (proposal.get("name") or "").strip()
    pattern = (proposal.get("pattern") or "").strip()
    if not name or not pattern:
        raise ExtractionError(f"proposal missing name or pattern: {proposal!r}")

    # Keep only ids the model was actually shown — provenance has to be checkable.
    provenance = [pid for pid in proposal.get("source_post_ids") or [] if pid in known_ids]

    return create_template(
        session,
        kind=TemplateKind.HOOK,
        name=name,
        body={
            "pattern": pattern,
            "tone": (proposal.get("tone") or "").strip(),
            "rationale": (proposal.get("rationale") or "").strip(),
            "cohort": cohort.value,
        },
        slots=proposal.get("slots") or [],
        provenance=provenance,
    )


def propose_hooks(
    session: Session,
    llm: LLM,
    *,
    platform: str = "linkedin",
    sample_size: int = DEFAULT_SAMPLE_SIZE,
    cohort: Cohort = Cohort.VOICE,
) -> list[Template]:
    """Propose hook templates from the strongest posts. Proposals only — a human approves.

    Ranked by engaged actions, so the model learns from posts people responded to rather
    than posts that merely reached far. `cohort` selects whose posts are read; a hook is a
    borrowable shape, so a creator's posts can teach one.
    """
    # Coerced at the boundary: Cohort is a StrEnum, so a bare "voice" compares equal to
    # Cohort.VOICE but fails the identity checks below and has no .value — it would route
    # silently to the wrong cohort. Normalising here keeps everything downstream an enum.
    cohort = Cohort(cohort)
    posts = _strongest_posts(session, platform, sample_size, cohort)
    if not posts:
        return []

    result = llm.complete_json(_SYSTEM, _build_prompt(posts))
    proposals = result.get("hooks")
    if not isinstance(proposals, list):
        raise ExtractionError(f"expected a 'hooks' list, got keys {sorted(result)}")

    known_ids = {p.zernio_id for p in posts}
    return [_to_template(session, proposal, known_ids, cohort) for proposal in proposals]


def _structure_prompt(posts: list[Post], hooks: list[Template], focus: str = "") -> str:
    lines = [
        "Posts, strongest first. 'engaged' is likes + comments + shares + saves — the "
        "measure that matters. Weight the top of this list most heavily.",
        "",
    ]
    for post in posts:
        lines.append(f"id: {post.zernio_id} | engaged: {post.engaged_actions}")
        lines.append(post.content.strip()[:POST_CHARS])
        lines.append("---")
    lines.append("")
    if focus:
        lines.append(
            f"Describe the structure for the '{focus}' post type specifically. Ground it in "
            f"whichever of the posts above are of that type, even if they are not the "
            f"strongest performers — say so in the rationale if the evidence is thin."
        )
        lines.append("")
    lines.append("Available hook templates you may cite in compatible_hooks:")
    lines.extend(f"- {hook.name}" for hook in hooks)
    if not hooks:
        lines.append("(none yet — return an empty compatible_hooks list)")
    return "\n".join(lines)


def _to_structure(
    session: Session, proposal: dict, hooks_by_name: dict[str, str], cohort: Cohort
) -> Template:
    name = (proposal.get("name") or "").strip()
    sections = proposal.get("sections") or []
    if not name or not sections:
        raise ExtractionError(f"structure missing name or sections: {proposal!r}")

    # Only hooks that actually exist — a structure pointing at an invented hook would
    # break generation the first time anyone selected it.
    families = [
        hooks_by_name[cited]
        for cited in proposal.get("compatible_hooks") or []
        if cited in hooks_by_name
    ]

    return create_template(
        session,
        kind=TemplateKind.STRUCTURE,
        name=name,
        body={
            "post_type": (proposal.get("post_type") or name).strip(),
            "sections": sections,
            "compatible_hook_families": families,
            "rationale": (proposal.get("rationale") or "").strip(),
            "cohort": cohort.value,
        },
        provenance=[p for p in proposal.get("source_post_ids") or []],
    )


def propose_structures(
    session: Session,
    llm: LLM,
    *,
    platform: str = "linkedin",
    sample_size: int = DEFAULT_SAMPLE_SIZE,
    focus: str = "",
    cohort: Cohort = Cohort.VOICE,
) -> list[Template]:
    """Propose post structures from the strongest posts. Proposals only — a human approves.

    `focus` names a post type to describe specifically. Without it the model reports the
    shapes the corpus actually rewards, which may not include a type you want covered.
    `cohort` selects whose posts are read; a structure is a borrowable shape, so a
    creator's posts can teach one.
    """
    # Coerced at the boundary: Cohort is a StrEnum, so a bare "voice" compares equal to
    # Cohort.VOICE but fails the identity checks below and has no .value — it would route
    # silently to the wrong cohort. Normalising here keeps everything downstream an enum.
    cohort = Cohort(cohort)
    posts = _strongest_posts(session, platform, sample_size, cohort)
    if not posts:
        return []

    hooks = usable_templates(session, TemplateKind.HOOK)
    result = llm.complete_json(_STRUCTURE_SYSTEM, _structure_prompt(posts, hooks, focus))
    proposals = result.get("structures")
    if not isinstance(proposals, list):
        raise ExtractionError(f"expected a 'structures' list, got keys {sorted(result)}")

    hooks_by_name = {hook.name: hook.family_id for hook in hooks}
    return [_to_structure(session, proposal, hooks_by_name, cohort) for proposal in proposals]


def compatible_hooks(session: Session, structure: Template) -> list[Template]:
    """The usable hooks this structure declares it pairs with."""
    families = set(structure.body.get("compatible_hook_families") or [])
    if not families:
        return []
    return [h for h in usable_templates(session, TemplateKind.HOOK) if h.family_id in families]
