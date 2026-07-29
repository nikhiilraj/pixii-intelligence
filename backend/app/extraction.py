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


class ExtractionError(RuntimeError):
    """The model's proposal could not be turned into templates."""


def _hook_of(content: str) -> str:
    opening = " / ".join(line.strip() for line in content.splitlines() if line.strip())
    return opening[:HOOK_CHARS]


def _strongest_posts(session: Session, platform: str, sample_size: int) -> list[Post]:
    """The sample the model learns from.

    Every exclusion is in the query, before the limit, so a post that cannot be evidence
    never costs a real post its slot.
    """
    statement = (
        select(Post)
        .where(Post.platform == platform)
        # Templates claim to describe one person's voice. Creator posts stay in the corpus
        # as reference material; they just do not get to shape that claim.
        .where(Post.account_username == settings.voice_account)
        # Held out by hand — strong for a reason that cannot repeat.
        .where(col(Post.excluded_from_extraction).is_(False))
        # A post with no text carries no hook, so it is no evidence.
        .where(func.trim(col(Post.content)) != "")
        .order_by(col(Post.engaged_actions).desc())
        .limit(sample_size)
    )
    return list(session.exec(statement).all())


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


def _to_template(session: Session, proposal: dict, known_ids: set[str]) -> Template:
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
) -> list[Template]:
    """Propose hook templates from the strongest posts. Proposals only — a human approves.

    Ranked by engaged actions, so the model learns from posts people responded to rather
    than posts that merely reached far.
    """
    posts = _strongest_posts(session, platform, sample_size)
    if not posts:
        return []

    result = llm.complete_json(_SYSTEM, _build_prompt(posts))
    proposals = result.get("hooks")
    if not isinstance(proposals, list):
        raise ExtractionError(f"expected a 'hooks' list, got keys {sorted(result)}")

    known_ids = {p.zernio_id for p in posts}
    return [_to_template(session, proposal, known_ids) for proposal in proposals]


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


def _to_structure(session: Session, proposal: dict, hooks_by_name: dict[str, str]) -> Template:
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
) -> list[Template]:
    """Propose post structures from the strongest posts. Proposals only — a human approves.

    `focus` names a post type to describe specifically. Without it the model reports the
    shapes the corpus actually rewards, which may not include a type you want covered.
    """
    posts = _strongest_posts(session, platform, sample_size)
    if not posts:
        return []

    hooks = usable_templates(session, TemplateKind.HOOK)
    result = llm.complete_json(_STRUCTURE_SYSTEM, _structure_prompt(posts, hooks, focus))
    proposals = result.get("structures")
    if not isinstance(proposals, list):
        raise ExtractionError(f"expected a 'structures' list, got keys {sorted(result)}")

    hooks_by_name = {hook.name: hook.family_id for hook in hooks}
    return [_to_structure(session, proposal, hooks_by_name) for proposal in proposals]


def compatible_hooks(session: Session, structure: Template) -> list[Template]:
    """The usable hooks this structure declares it pairs with."""
    families = set(structure.body.get("compatible_hook_families") or [])
    if not families:
        return []
    return [h for h in usable_templates(session, TemplateKind.HOOK) if h.family_id in families]
