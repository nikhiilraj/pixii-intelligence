from sqlmodel import Session, col, select

from app.llm import LLM
from app.models.post import Post
from app.models.template import Template, TemplateKind
from app.templates import create_template

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


class ExtractionError(RuntimeError):
    """The model's proposal could not be turned into templates."""


def _hook_of(content: str) -> str:
    opening = " / ".join(line.strip() for line in content.splitlines() if line.strip())
    return opening[:HOOK_CHARS]


def _strongest_posts(session: Session, platform: str, sample_size: int) -> list[Post]:
    statement = (
        select(Post)
        .where(Post.platform == platform)
        .order_by(col(Post.engaged_actions).desc())
        .limit(sample_size)
    )
    # A post with no text carries no hook, so it is no evidence.
    return [p for p in session.exec(statement).all() if p.content.strip()]


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
