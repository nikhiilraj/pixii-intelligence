from enum import StrEnum

from sqlalchemy import func
from sqlmodel import Session, col, select

from app.config import settings
from app.llm import LLM
from app.models.post import Post
from app.models.template import Template, TemplateKind
from app.templates import create_template, usable_templates

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


_VISUAL_SYSTEM = "Placeholder — Task 4 writes the real extraction prompt."


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

    llm.complete_json(_VISUAL_SYSTEM, _visual_prompt(sample), [raw for _, raw in sample])
    return []


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
