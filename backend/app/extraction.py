import io
import logging
import math
from enum import StrEnum

from PIL import Image
from sqlalchemy import func
from sqlmodel import Session, col, select

from app import prompts
from app.config import settings
from app.generation import render_template
from app.llm import LLM
from app.models.post import Post
from app.models.template import Template, TemplateKind, TemplateStatus
from app.prompts.tracing import new_correlation_id, traced_call
from app.rendering import DEFAULT_HEIGHT, DEFAULT_WIDTH, SLOT, HtmlRenderer
from app.templates import (
    approve,
    create_template,
    edit_template,
    latest_versions,
    usable_templates,
)

log = logging.getLogger(__name__)

# Filtered provenance at or above which a hook or a structure is written APPROVED rather than
# PROPOSED.
#
# Not a ranking. Nothing is called best and nothing is sorted; the only question asked is
# whether the corpus repeats a shape often enough that it is a pattern rather than a
# transcription of one post — hook provenance averaged 1.09 posts per template under the old
# twelve-post sample, so anything above 1 separates the two.
#
# **5 is a guess, and it has never been run against a real corpus.** The design names the read
# that earns it (`docs/superpowers/specs/2026-08-06-corpus-wide-extraction-design.md`): open
# three posts a pattern claims to cover and ask whether the pattern describes *those posts*.
# It may well move afterwards.
#
# Deliberately not `settings.min_sample_size`, which also happens to be 5. That one counts
# published drafts for metrics attribution; two unrelated fives sharing one name is a silent
# bug the day either of them moves.
APPROVE_AT_COVERAGE = 5

# The model is told to return at most 10, and this is what makes that true rather than
# hoped-for. Minting a family per proposal is what put 42 hook families in the database; an
# unbounded batch is that defect with a wider sample behind it. Truncation is never silent —
# see the warning in `propose_hooks`.
MAX_HOOK_PROPOSALS = 10

# The same bound on structures, and a separate constant rather than a shared one: the two
# prompts each state their own limit, and a single number would silently change what the other
# asks for the day one of them moves. Truncation is never silent — see `propose_structures`.
MAX_STRUCTURE_PROPOSALS = 10

# Fewer than a hook sample: every entry here is a full image in the request, and a layout
# repeats visibly across far fewer examples than a sentence pattern does.
VISUAL_SAMPLE_SIZE = 5

# Bounds how long the extract route can take: each one costs a render, and a rate-limited
# render costs 35s of backoff.
MAX_VISUAL_PROPOSALS = 5

# Suffixes `media.download_post_media` stores that are still images. A video frame is not
# the artefact — the post's picture is — so `.mp4`, `.mov` and `.webm` are not here.
_STILL_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".webp")

# The opening is the hook. Sending whole posts buries it and wastes context.
HOOK_CHARS = 220

# The three prompts this file sends, from the registry rather than from constants here. Named
# once, at module level, for the reason `generation.py` gives beside its own: the version a
# file sends is then a fact about the file, and the trace row a call writes cannot name a
# prompt some other line chose. `prompts/extraction.py` carries the texts and the brand rules
# they interpolate.
_HOOKS = prompts.get("extraction.hooks", "2.0.0")
_STRUCTURES = prompts.get("extraction.structures", "2.0.0")
_VISUALS = prompts.get("extraction.visuals", "1.0.0")

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
    sample_size: int | None,
    cohort: Cohort,
    require_content: bool = True,
) -> list[Post]:
    """The sample the model learns from.

    Every exclusion is in the query, before the limit, so a post that cannot be evidence
    never costs a real post its slot.

    **`sample_size=None` is whole-corpus mode: no ordering and no limit.** The name still
    reads as the ranked mode because `_visual_sample` still asks for it — hooks and structures
    both pass None. In the None case it is every non-excluded post of the cohort, in whatever
    order Postgres returns them. "What is common across the corpus" and "what do the strongest
    posts do" are different questions, and ranking the sample only answers the second one.
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
    if sample_size is not None:
        # Ordering and limit are one decision, not two: an unlimited query has nothing to
        # rank *for*, and a ranked query without a limit still hands the model a "strongest
        # first" claim that whole-corpus extraction must not make.
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


class _RejectedProposal(RuntimeError):
    """One proposal is unusable. The others in the batch are not."""


def _unstorable(value: object) -> str | None:
    """Why Postgres would refuse something inside `value`, or None if it would take it.

    `json.loads` is more permissive than the database on three counts, and nothing in
    between objects. `NaN`, `Infinity` and `-Infinity` are a documented stdlib extension
    to the JSON spec; `"\\u0000"` and `"\\ud800"` are legal JSON escapes that decode to a
    Python string Python is perfectly happy to hold. Each one is refused only at
    `session.flush()`, as a `DataError` that `except _RejectedProposal` does not catch —
    so one of them in one proposal costs every sibling in the batch its slot.

    Probes an entire proposal: every value at any depth, and dict keys as well as values.
    Deliberately not the fields that reach the database today. Four rounds of this defect
    were each closed by naming the field that had just broken, and the next round found it
    in a field none of them had named; a probe that must be widened whenever the write
    changes is the same bug with a delay on it. The cost is that a proposal can be dropped
    for a character in a field nothing would have written — an acceptable trade, since the
    only characters this rejects are ones no usable layout contains.

    Known gap, left open deliberately: *depth*, not content. Psycopg serialises with
    `json.dumps`, which recurses in C and blows the stack below the 1497-level ceiling
    `json.loads` allows, so a deeply nested proposal still raises `RecursionError` at
    flush rather than being rejected here — the same batch-killing shape as everything
    above. It is unfixed because there is no exact check to write: the safe threshold is
    not a constant but a function of how deep the call stack already is when psycopg
    encodes: the shallowest failure measured was 1480 under pytest, while a standalone
    script still stored 1490 and only broke at 1497. So any depth limit is a guess with a
    margin that can be wrong in another call context. That is
    categorically weaker than the exact checks around it. Not unreachable, either —
    degenerate repetition from a model can plausibly emit >1500 nested brackets within a
    16k token budget — but unlikely, and one lost batch is recoverable.
    """
    # Iterative, not recursive, and that is not a style choice. `json.loads` accepts up to
    # 1497 levels of nesting, a slot value may be nested arbitrarily deep, and a recursive
    # walk over a 1000-deep proposal raises RecursionError (measured, both of them). That
    # is not a `_RejectedProposal` either, so the obvious recursive version of this
    # function would kill the batch in exactly the way it exists to prevent.
    stack: list[object] = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, str):
            # Checked before the encode below because a NUL encodes to UTF-8 perfectly
            # well; it is Postgres that has no room for it. Two layers, one cause: in
            # JSONB the server raises UntranslatableCharacter, while a text column never
            # reaches the server at all, since psycopg refuses to put a NUL in a
            # parameter. Serialising and scanning the result cannot do this job —
            # `json.dumps` escapes a NUL to the six characters `\u0000` even under
            # `ensure_ascii=False`, which is indistinguishable from text that spells that
            # escape out literally.
            if "\x00" in item:
                return f"NUL (U+0000) in {item!r}"
            try:
                # The encode the driver has to perform, performed here where it can be
                # caught rather than reimplemented as a surrogate-range test that could
                # drift from it. `json.loads` combines a surrogate *pair* into the real
                # character, so anything still in that range arrived unpaired and has no
                # UTF-8 encoding at all.
                item.encode("utf-8")
            except UnicodeEncodeError as exc:
                return f"{exc}, in {item!r}"
            # `!r` on both, because this string ends up in a `_RejectedProposal` that
            # `propose_visuals` hands to `log.warning`. Not a crash guard: `logging`
            # catches a failed write inside the handler and routes it to `handleError`,
            # so it would never have reached the caller. What it costs is the message —
            # on a strict-encoding stream the line is replaced by a `--- Logging error ---`
            # traceback on stderr (measured), and elsewhere a NUL or lone surrogate renders
            # as mojibake or silently vanishes. The reason a proposal was dropped is the
            # only record anyone gets of it, so it is worth keeping legible; `repr` escapes
            # both triggers to ASCII.
        elif isinstance(item, float) and not math.isfinite(item):
            return f"{item!r} is not a finite number"
        elif isinstance(item, dict):
            # Keys as well as values: a NUL in a slot's key was measured to fail exactly
            # as one in its value does, and only this branch can reach it.
            stack.extend(item.keys())
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)
    # Everything else `json.loads` can produce — int, bool, None — has no unstorable
    # value: JSONB holds an integer of any magnitude as `numeric` (checked with 10**400).
    return None


def _to_visual(
    session: Session, proposal: dict, sizes: dict[str, tuple[int, int]], cohort: Cohort
) -> Template:
    # `proposal` is annotated `dict` and is not one — it is an element of a list parsed by
    # `json.loads` from a model response, so it can be any JSON type. A type-checker will
    # call this guard dead code. It is not: the annotation is the lie, not the check.
    # ponytail: annotation left loose to match `_to_template` and `_to_structure`, which
    # take the same untyped element the same way. Widen all three together or none.
    if not isinstance(proposal, dict):
        raise _RejectedProposal(f"proposal is not an object: {proposal!r}")

    # Probed before `name` and `markup` are read, so every rejection message below is built
    # from text that has already been cleared. Not a crash guard — `logging` swallows a
    # failed write inside the handler and would not have taken the batch down. It keeps the
    # messages legible: a NUL or a lone surrogate reaching a log line renders as mojibake
    # or vanishes, and the reason a proposal was dropped is the only record anyone gets.
    unstorable = _unstorable(proposal)
    if unstorable:
        raise _RejectedProposal(f"proposal is not storable: {unstorable}")

    name = str(proposal.get("name") or "").strip()
    markup = str(proposal.get("html") or "").strip()
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

    sources = proposal.get("source_post_ids") or []
    if not isinstance(sources, list):
        raise _RejectedProposal(f"{name}: source_post_ids must be a list, got {sources!r}")
    # Keep only ids the model was actually shown — provenance has to be checkable.
    #
    # `isinstance(pid, str)` is not fussiness about types. `pid in sizes` raises
    # `TypeError: unhashable type` on a list or dict element, and a TypeError is not a
    # `_RejectedProposal` — so one nested list in one proposal would kill the whole batch,
    # which is precisely what the rejection path exists to prevent.
    provenance = [pid for pid in sources if isinstance(pid, str) and pid in sizes]
    # The size is the source's, and only when there is exactly one source to take it from.
    # Averaging two sizes would invent a third that no post ever used.
    width, height = sizes[provenance[0]] if len(provenance) == 1 else (
        DEFAULT_WIDTH,
        DEFAULT_HEIGHT,
    )

    # Pinned on the slot's declared role, never its name. `left_image_url` reads as an
    # asset and `hero` does not, so a name heuristic here reintroduces exactly the silent
    # wrongness the typed-slot design closes.
    if settings.brand_logo_asset_id is not None:
        slots = [
            {**slot, "default_asset_id": settings.brand_logo_asset_id}
            if slot.get("role") == "logo"
            else slot
            for slot in slots
        ]

    return create_template(
        session,
        kind=TemplateKind.VISUAL,
        name=name,
        body={
            "renderer": "html",
            "html": markup,
            "width": width,
            "height": height,
            "rationale": str(proposal.get("rationale") or "").strip(),
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
    renderer: HtmlRenderer,
    *,
    platform: str = "linkedin",
    sample_size: int = VISUAL_SAMPLE_SIZE,
    cohort: Cohort = Cohort.VOICE,
    correlation_id: str | None = None,
) -> list[Template]:
    """Propose visual layouts from the images of the strongest posts. Proposals only.

    Every proposal is rendered once before it is offered. A layout that does not render is
    not a proposal — it is a trap with a human's approval on it, and there are already 50
    proposals waiting in that queue.

    **This route is slow, and that is expected rather than a hang.** One LLM call plus one
    Cloudflare render per proposal, and `CloudflareRenderer` absorbs a per-minute 429 with
    5 + 10 + 20 = 35s of backoff. `MAX_VISUAL_PROPOSALS` bounds the worst case.
    ponytail: rendering at approval time instead was considered and rejected — it moves
    the failure to the moment a human has already said yes.
    """
    cohort = Cohort(cohort)
    sample = _visual_sample(session, platform, sample_size, cohort)
    if not sample:
        return []

    result = traced_call(
        session,
        llm,
        _VISUALS,
        _visual_prompt(sample),
        correlation_id=correlation_id or new_correlation_id(),
        input_artifact_ids={
            "posts": ",".join(post.zernio_id for post, _ in sample),
            "cohort": cohort.value,
            "platform": platform,
        },
        # The images go down as they always did. `traced_call` takes them because this call
        # site exists: a tracing wrapper that could not carry images would have left the one
        # vision prompt in the application as the one prompt nothing recorded.
        images=[raw for _, raw in sample],
    )
    proposals = result.get("visuals")
    if not isinstance(proposals, list):
        raise ExtractionError(f"expected a 'visuals' list, got keys {sorted(result)}")

    sizes = {post.zernio_id: _size_of(raw) for post, raw in sample}
    kept: list[Template] = []
    for proposal in proposals[:MAX_VISUAL_PROPOSALS]:
        try:
            template = _to_visual(session, proposal, sizes, cohort)
            _must_render(session, template, renderer)
        except Exception as exc:  # noqa: BLE001
            # Deliberately broad, and the third attempt at this. The gate asks one
            # question — can this layout be drawn? — and every "no" is equivalent: a
            # missing slot, an unresolvable asset, a 500 from Cloudflare, a connection
            # that never opened. Two narrower versions of this line each shipped one
            # exception type short, and each time the cost was every *other* proposal in
            # the batch. `_draw_visual` takes the same catch for the same reason.
            #
            # The type is logged alongside the message because the catch is this broad: a
            # `_RejectedProposal` from a malformed model response and an `AttributeError`
            # from a real bug in `_to_visual` would otherwise read identically in the log,
            # and the type is the only thing that tells them apart.
            log.warning("visual proposal rejected: %s: %s", type(exc).__name__, exc)
            continue
        kept.append(template)
    if len(proposals) > MAX_VISUAL_PROPOSALS:
        # Never silently. A truncated batch that reads as a complete one is how a partial
        # answer gets mistaken for the whole picture. "considered", not "kept": rejections
        # among the first MAX_VISUAL_PROPOSALS mean fewer than that may end up in `kept`.
        log.warning(
            "model returned %d visuals; considered the first %d",
            len(proposals),
            MAX_VISUAL_PROPOSALS,
        )
    return kept


def _must_render(session: Session, template: Template, renderer: HtmlRenderer) -> None:
    """Render the proposal from its own slot examples, or reject it.

    The examples are what the model wrote down as representative, so they are the honest
    values to prove the layout with — the same ones the template editor previews from.

    **A pinned `image_url` slot is left out of `values`.** `chosen_assets` lets a supplied
    value win over `default_asset_id`, so a slot's own example would always outrank the
    pin and the gate would render the placeholder URL forever, never the real logo. Leaving
    the slot absent here lets `chosen_assets` fall through to the pin instead, which is what
    makes this the same render the approved template will actually produce — so a
    `brand_logo_asset_id` naming an asset that does not exist is caught here, before a human
    approves the template, rather than at the first real generation.
    """
    values = {
        str(slot.get("name")): str(slot.get("example") or slot.get("name") or "")
        for slot in template.slots
        if not (slot.get("type") == "image_url" and slot.get("default_asset_id") is not None)
    }
    render_template(session, template, values, renderer)


def _build_prompt(posts: list[Post], families: list[Template]) -> str:
    """Every post's opening, unordered and with no engagement figure attached, then the
    families a pattern may be reconciled into.

    Both removals are load-bearing rather than tidying. The old preamble said "strongest
    first" and "weight the top of this list most heavily"; with no `order_by` in the query
    the first is simply false, and the second would put the ranking back into the user
    message the moment SQL stopped doing it — leaving engagement steering extraction from a
    place nobody would think to grep. `engaged` is gone from the per-post line for the same
    reason: it cannot answer "what repeats across the corpus", which is the only question
    this prompt now asks.

    The family list is the same mechanism `_structure_prompt` uses to let a structure cite a
    hook by name, pointed at the hook's own kind — with the id alongside the name, because
    the id is what `(family_id, version)` lineage resolves through and two runs can easily
    give one shape two names.
    """
    lines = ["Every post in the corpus, in no particular order.", ""]
    for post in posts:
        lines.append(f"id: {post.zernio_id}")
        lines.append(f"hook: {_hook_of(post.content)}")
        lines.append("")
    lines.append(
        "Hook families that already have names. If one of your patterns is the same shape as "
        "one of these, return its family_id rather than inventing a new name:"
    )
    lines.extend(f"- {family.name} | family_id: {family.family_id}" for family in families)
    if not families:
        lines.append("(none yet — every pattern you return starts a new family)")
    return "\n".join(lines)


def _to_template(
    session: Session,
    proposal: dict,
    known_ids: set[str],
    cohort: Cohort,
    families: dict[str, Template],
) -> Template:
    name = (proposal.get("name") or "").strip()
    pattern = (proposal.get("pattern") or "").strip()
    if not name or not pattern:
        # `_RejectedProposal`, not `ExtractionError`, and the distinction is not cosmetic.
        # `ExtractionError` is the *batch* contract: `api_templates.py:144` turns it into a
        # 502, which is right for "the model returned no hooks list" and wrong for "one
        # proposal of ten is blank". Both are caught by the broad `except` in
        # `propose_hooks` today, so raising the wrong one costs nothing until someone
        # narrows that catch — and the comment in `propose_visuals` records that being
        # narrowed twice, each time taking the whole batch down with it.
        raise _RejectedProposal(f"proposal missing name or pattern: {proposal!r}")

    # Keep only ids the model was actually shown — provenance has to be checkable.
    provenance = [pid for pid in proposal.get("source_post_ids") or [] if pid in known_ids]
    if not provenance:
        # A pattern that covers nothing the model was shown is not a pattern, and since the
        # prompt now *requires* `source_post_ids`, accepting one anyway would make the schema
        # a stricter description than the code — the mirror of the reason 1.0.0's schema
        # demands so little. It also keeps a zero-coverage row out of the count below.
        #
        # **Checked before the family lookup, and the order is deliberate.** Reconciling first
        # would write a version 2 replacing a family's real provenance with an empty one;
        # dropping the proposal costs that family a version it can get back on the next run.
        raise _RejectedProposal(f"{name}: no cited post id was in the sample: {proposal!r}")

    body = {
        "pattern": pattern,
        "tone": (proposal.get("tone") or "").strip(),
        "rationale": (proposal.get("rationale") or "").strip(),
        "cohort": cohort.value,
    }
    slots = proposal.get("slots") or []
    # Counted off the *filtered* list, never off what the model claimed. Provenance is the
    # approval gate now, so five invented ids would auto-approve a template covering nothing —
    # and invented ids are measured rather than feared: all three VISUAL citations in the
    # database today join to no post row.
    covered = len(provenance) >= APPROVE_AT_COVERAGE

    # `str(...)`, not a cast the type-checker asked for: `families.get` on a list or dict
    # raises `TypeError: unhashable type`, which is not a `_RejectedProposal` — the same trap
    # `_to_visual` records paying for on `pid in sizes`.
    existing = families.get(str(proposal.get("family_id") or ""))
    if existing is None:
        # No id cited, or one naming nothing. The unknown-id case creates rather than drops,
        # and that is a decision rather than a fallthrough: the model invented a *name for the
        # family*, not the pattern or its coverage, and dropping the proposal would throw away
        # evidence over a bad label. A duplicate family the next run can merge by citing the
        # right id is the cheaper mistake. It is not how a retired family comes back — a
        # retired family is in `families`, so it reaches `edit_template` and raises below.
        return create_template(
            session,
            kind=TemplateKind.HOOK,
            name=name,
            body=body,
            slots=slots,
            provenance=provenance,
            status=TemplateStatus.APPROVED if covered else TemplateStatus.PROPOSED,
        )

    # Raises `RetiredTemplateError` when the family was retired, and nothing here catches it:
    # the per-proposal catch in `propose_hooks` logs and drops it, which is the whole handling
    # a retired family needs. Anything that fell back to `create_template` here would revive it
    # under a new id, silently, which is exactly what retiring it said not to do.
    revised = edit_template(
        session, existing, name=name, body=body, slots=slots, provenance=provenance
    )
    if covered and revised.status is TemplateStatus.PROPOSED:
        # Explicit, because `edit_template` copies the status of the row it revises
        # (`templates.py:69`) — so without this a family that landed at coverage 3 stays
        # PROPOSED forever, even when a later run finds it covering forty posts. Coverage
        # growing as posts arrive is the entire point of reading the whole corpus.
        #
        # Promotion only. APPROVED is never touched whatever coverage does, and nothing here
        # demotes or retires: a run that finds less than the last one is evidence about that
        # run, not grounds for pulling a template out of the library.
        approve(session, revised)
    return revised


def propose_hooks(
    session: Session,
    llm: LLM,
    *,
    platform: str = "linkedin",
    cohort: Cohort = Cohort.VOICE,
    # Minted here when the caller does not supply one, so a trace row always groups the
    # calls of one extraction rather than sitting alone. `POST /templates/extract` runs one
    # kind at a time, so one call is usually the whole operation — but the id is the seam
    # that makes "everything one extract route bought" answerable the day it runs two.
    correlation_id: str | None = None,
) -> list[Template]:
    """Propose hook patterns from every non-excluded post of the cohort.

    The whole corpus, unranked, and the model is asked what *recurs* across it rather than
    what the strongest twelve posts do. `cohort` selects whose posts are read; a hook is a
    borrowable shape, so a creator's posts can teach one.

    **`sample_size` is gone rather than defaulted, and that is deliberate.** There is no
    smaller sample to ask for, and a parameter that silently does nothing is the failure
    this file's comments exist to prevent.

    **A pattern the model recognises as an existing family gains a version of that family
    rather than a sibling of it.** The families that already have names go into the request
    with their ids, and a proposal citing one is written through `edit_template`. Minting a
    family per proposal is the whole explanation for 42 hook families drawn from roughly 12
    posts, and nothing in this path used to look an existing template up at all.

    A pattern covering `APPROVE_AT_COVERAGE` posts or more is written APPROVED — including a
    revision of a family that was PROPOSED when the last run left it, which is a promotion
    `edit_template` cannot make on its own. The rest arrive PROPOSED for an optional human
    look. Nothing here demotes or retires anything: the append-only write is what protects
    "nothing is lost", not the click.
    """
    # Coerced at the boundary: Cohort is a StrEnum, so a bare "voice" compares equal to
    # Cohort.VOICE but fails the identity checks below and has no .value — it would route
    # silently to the wrong cohort. Normalising here keeps everything downstream an enum.
    cohort = Cohort(cohort)
    posts = _strongest_posts(session, platform, None, cohort)
    if not posts:
        return []

    # Two lists out of one query, and they are deliberately different sets.
    #
    # `families` is what a cited id is resolved against and keeps RETIRED rows: that is what
    # sends a proposal citing a retired family into `edit_template`, which raises and drops it.
    # Leaving them out would route the same proposal to `create_template` and revive the family
    # under a new id — the one thing retiring it forbade.
    #
    # `offered` is what the model is shown and drops them, for the reason `usable_templates`
    # gives: a withdrawn template is not offered. Advertising an id whose every citation is
    # discarded would lose a real pattern on every run.
    #
    # ponytail: cohort-blind, unlike the sample, which never mixes cohorts. A VOICE run and an
    # INSPIRATION run see the same families, so a family cited by both ends up with whichever
    # cohort and provenance ran last rather than the union — `edit_template` replaces both
    # fields wholesale. Tolerable because a hook is a borrowable shape either way, and because
    # `generation._exemplars` enforces the voice rule independently of what a template claims.
    # Filtering on `body["cohort"]` here would be worse than the flip it fixes: the 42 legacy
    # families this reconciliation exists to collapse were written before that key existed, so
    # the filter would hide exactly them. Scope families per cohort once every row carries one.
    families = {t.family_id: t for t in latest_versions(session, TemplateKind.HOOK)}
    offered = [t for t in families.values() if t.status is not TemplateStatus.RETIRED]

    result = traced_call(
        session,
        llm,
        _HOOKS,
        _build_prompt(posts, offered),
        correlation_id=correlation_id or new_correlation_id(),
        # The posts the proposal is grounded in, so a template a human later approves can be
        # traced back to the sample that produced it. Zernio ids, joined, because that is what
        # `Template.provenance` stores and what `_to_template` checks the model's citations
        # against — a different identifier here would not join to anything.
        input_artifact_ids={
            "posts": ",".join(p.zernio_id for p in posts),
            # The families the model was allowed to reconcile into, for the reason
            # `propose_structures` records the hook list it showed: a template that gained a
            # version gained it because this call named its family, so which list it was shown
            # is part of how that version came about. `(family_id, version)`, never the id
            # alone — the id does not say which version of the family the model was reading.
            "families": ",".join(f"{t.family_id}/{t.version}" for t in offered),
            "cohort": cohort.value,
            "platform": platform,
        },
    )
    proposals = result.get("hooks")
    if not isinstance(proposals, list):
        raise ExtractionError(f"expected a 'hooks' list, got keys {sorted(result)}")

    known_ids = {p.zernio_id for p in posts}
    kept: list[Template] = []
    if len(proposals) > MAX_HOOK_PROPOSALS:
        # Never silently. A truncated batch that reads as a complete one is how a partial
        # answer gets mistaken for the whole picture. "considered", not "kept": rejections
        # among the first MAX_HOOK_PROPOSALS mean fewer than that may end up in `kept`.
        log.warning(
            "model returned %d hooks; considered the first %d",
            len(proposals),
            MAX_HOOK_PROPOSALS,
        )
    for proposal in proposals[:MAX_HOOK_PROPOSALS]:
        try:
            template = _to_template(session, proposal, known_ids, cohort, families)
        except Exception as exc:  # noqa: BLE001
            # Deliberately broad, and for the reason `propose_visuals` records paying for
            # twice: every "no" to "can this proposal be stored?" is equivalent, and the
            # cost of missing one exception type is every *other* proposal in the batch.
            # A model that returns a bare string where an object belongs raises
            # `AttributeError` inside `_to_template`, not `_RejectedProposal`.
            #
            # The type is logged alongside the message because the catch is this broad: a
            # `_RejectedProposal` from a malformed response and an `AttributeError` from a
            # real bug in `_to_template` would otherwise read identically in the log.
            log.warning("hook proposal rejected: %s: %s", type(exc).__name__, exc)
            continue
        kept.append(template)
        # What this batch wrote is visible to the rest of it, and that is not bookkeeping.
        # Two proposals may cite one family; against the snapshot taken before the loop the
        # second would revise the *old* row, and `edit_template` copies that row's status — so
        # a second proposal at coverage 1 would write a PROPOSED version straight over the
        # APPROVED one the first just earned. Nothing here may demote anything.
        families[template.family_id] = template
    return kept


def _structure_prompt(
    posts: list[Post], hooks: list[Template], families: list[Template], focus: str = ""
) -> str:
    """Every post in full, unordered and with no engagement figure attached, then the hooks a
    structure may pair with and the families it may be reconciled into.

    Both removals are the ones `_build_prompt` records for hooks, for the same reasons: with no
    `order_by` in the query "strongest first" is simply false, and "weight the top of this list
    most heavily" would leave engagement steering extraction from the user message after SQL
    stopped doing it. The focus paragraph carried the same claim in its own words ("even if they
    are not the strongest performers") and loses it too — a phrase in a branch is still a phrase
    in the prompt.

    **The hooks are listed with their family ids, and that is a fix rather than a tidy.** This
    list used to carry names alone and `_to_structure` resolved `compatible_hooks` through them.
    A hook's name is no longer stable: `_to_template` writes `edit_template(name=...)` from the
    proposal, so version 2 of a family can be called something else, and a name-keyed lookup
    would silently pair a structure with nothing the first time that happened. Stored structures
    were never at risk — `compatible_hook_families` has always held ids.

    Two lists of ids in one prompt, so they are labelled apart — and which list gets the bare
    label is deliberate. `family_id` means *the family this proposal may be a new version of*
    in `_build_prompt`, and it has to mean the same thing here or one concept carries opposite
    labels in two prompts a model reads back to back. So the reconciliation list keeps it and
    the hooks are qualified, even though the hook list is the one that was already here. A
    structure citing a hook's id as its own `family_id` would reach `families.get`, miss, and
    mint a new family every run — the exact defect this reconciliation exists to close, and
    invisible: nothing downstream can tell that id from a real miss.
    """
    lines = ["Every post in the corpus, in no particular order.", ""]
    for post in posts:
        lines.append(f"id: {post.zernio_id}")
        lines.append(post.content.strip()[:POST_CHARS])
        lines.append("---")
    lines.append("")
    if focus:
        lines.append(
            f"Describe the structure for the '{focus}' post type specifically. Ground it in "
            f"whichever of the posts above are of that type, however few there are — say so in "
            f"the rationale if the evidence is thin."
        )
        lines.append("")
    lines.append(
        "Hook templates you may pair with in compatible_hooks. Cite the hook family_id, never "
        "the name, and never as your own family_id:"
    )
    lines.extend(f"- {hook.name} | hook family_id: {hook.family_id}" for hook in hooks)
    if not hooks:
        lines.append("(none yet — return an empty compatible_hooks list)")
    lines.append("")
    lines.append(
        "Structure families that already have names. If one of your structures is the same "
        "shape as one of these, return its family_id rather than inventing a new name:"
    )
    lines.extend(f"- {family.name} | family_id: {family.family_id}" for family in families)
    if not families:
        lines.append("(none yet — every structure you return starts a new family)")
    return "\n".join(lines)


def _to_structure(
    session: Session,
    proposal: dict,
    hook_families: set[str],
    known_ids: set[str],
    cohort: Cohort,
    families: dict[str, Template],
) -> Template:
    name = (proposal.get("name") or "").strip()
    sections = proposal.get("sections") or []
    if not name or not sections:
        # `_RejectedProposal`, not `ExtractionError` — see `_to_template` for why the two
        # are not interchangeable.
        raise _RejectedProposal(f"structure missing name or sections: {proposal!r}")

    # Keep only ids the model was actually shown — provenance has to be checkable, the
    # same filter `_to_template` applies. "Exists in the database" is not the test: a real
    # post held out of this sample is one the pattern was never derived from, and it fails
    # only this check.
    provenance = [pid for pid in proposal.get("source_post_ids") or [] if pid in known_ids]
    if not provenance:
        # Every one of the 15 structures in the library cites nothing, because 1.0.0's example
        # JSON never showed `source_post_ids` and its schema never required it. 2.0.0 does
        # both, and this line is what makes that true rather than documentation: `traced_call`
        # does not validate a response against the schema, so accepting an uncited structure
        # anyway would keep minting exactly those rows under a prompt that says it cannot.
        #
        # **Checked before the family lookup, for the reason `_to_template` records:**
        # reconciling first would write a version 2 replacing a family's real provenance with
        # an empty one.
        raise _RejectedProposal(f"{name}: no cited post id was in the sample: {proposal!r}")

    # Only hooks that actually exist — a structure pointing at an invented hook would
    # break generation the first time anyone selected it.
    #
    # Matched on `family_id`, never on the name, and that is a fix. Reconciliation made a
    # hook's name mutable: `_to_template` writes `edit_template(name=...)` from the model's
    # proposal, so version 2 of a family can be called something else. A name-keyed lookup
    # would resolve to nothing the first time that happened, and silently — the stored row
    # holds family ids, so a structure that paired with nothing reads exactly like one that
    # cited no hooks. `_structure_prompt` lists the ids to match.
    cited_hooks = [
        cited for cited in proposal.get("compatible_hooks") or [] if cited in hook_families
    ]

    body = {
        "post_type": (proposal.get("post_type") or name).strip(),
        "sections": sections,
        "compatible_hook_families": cited_hooks,
        "rationale": (proposal.get("rationale") or "").strip(),
        "cohort": cohort.value,
    }
    # Counted off the *filtered* list, never off what the model claimed — see `_to_template`.
    covered = len(provenance) >= APPROVE_AT_COVERAGE

    # `str(...)` for the reason `_to_template` records: `families.get` on a list or dict raises
    # `TypeError: unhashable type`, which is not a `_RejectedProposal`.
    existing = families.get(str(proposal.get("family_id") or ""))
    if existing is None:
        return create_template(
            session,
            kind=TemplateKind.STRUCTURE,
            name=name,
            body=body,
            provenance=provenance,
            status=TemplateStatus.APPROVED if covered else TemplateStatus.PROPOSED,
        )

    # Raises `RetiredTemplateError` when the family was retired, and nothing here catches it —
    # the per-proposal catch in `propose_structures` logs and drops it, which is the whole
    # handling a retired family needs. See `_to_template`.
    revised = edit_template(session, existing, name=name, body=body, provenance=provenance)
    if covered and revised.status is TemplateStatus.PROPOSED:
        # Explicit, because `edit_template` copies the status of the row it revises
        # (`templates.py:69`). Promotion only: APPROVED is never touched and nothing here
        # demotes or retires. Again, `_to_template` carries the full reasoning.
        approve(session, revised)
    return revised


def propose_structures(
    session: Session,
    llm: LLM,
    *,
    platform: str = "linkedin",
    focus: str = "",
    cohort: Cohort = Cohort.VOICE,
    correlation_id: str | None = None,
) -> list[Template]:
    """Propose post structures from every non-excluded post of the cohort, in full text.

    Whole posts rather than openings, because a structure is about the shape of the entire
    thing. `cohort` selects whose posts are read; a structure is a borrowable shape, so a
    creator's posts can teach one.

    **`sample_size` is gone rather than defaulted.** Its docstring sold it as the way to reach
    post types below the engagement cutoff; there is no cutoff left to reach below, and a
    parameter that silently does nothing is the failure this file's comments exist to prevent.
    `focus` stays and still works — naming a post type to describe is a real request, and the
    only one the deleted parameter was ever standing in for.

    Reconciliation, coverage and promotion are `propose_hooks`' exactly, pointed at STRUCTURE;
    the reasoning for each is written out there and beside `_to_template` rather than repeated.
    The one thing that is new here: every structure now arrives with a non-empty provenance,
    where all 15 in the library today cite nothing at all.
    """
    # Coerced at the boundary: Cohort is a StrEnum, so a bare "voice" compares equal to
    # Cohort.VOICE but fails the identity checks below and has no .value — it would route
    # silently to the wrong cohort. Normalising here keeps everything downstream an enum.
    cohort = Cohort(cohort)
    posts = _strongest_posts(session, platform, None, cohort)
    if not posts:
        return []

    hooks = usable_templates(session, TemplateKind.HOOK)

    # Two lists out of one query, deliberately different sets, for the reason `propose_hooks`
    # writes out in full: `families` keeps RETIRED rows so a proposal citing one reaches
    # `edit_template` and is dropped rather than reviving the family under a new id, and
    # `offered` drops them because a withdrawn template is not advertised.
    #
    # ponytail: cohort-blind, unlike the sample. Same trade `propose_hooks` records and the same
    # upgrade path — scope families per cohort once every row carries a `body["cohort"]`, which
    # the 15 legacy structures this reconciliation exists to collapse do not.
    families = {t.family_id: t for t in latest_versions(session, TemplateKind.STRUCTURE)}
    offered = [t for t in families.values() if t.status is not TemplateStatus.RETIRED]

    result = traced_call(
        session,
        llm,
        _STRUCTURES,
        _structure_prompt(posts, hooks, offered, focus),
        correlation_id=correlation_id or new_correlation_id(),
        input_artifact_ids={
            "posts": ",".join(p.zernio_id for p in posts),
            # The hooks the model was allowed to cite. A structure naming a hook family is a
            # pairing a later draft is generated through, so which list it was shown is part
            # of how that pairing came about.
            "hooks": ",".join(f"{h.family_id}/{h.version}" for h in hooks),
            # And the structure families it was allowed to reconcile into — `(family_id,
            # version)`, never the id alone, exactly as `propose_hooks` records its own.
            "families": ",".join(f"{t.family_id}/{t.version}" for t in offered),
            "cohort": cohort.value,
            "platform": platform,
            "focus": focus,
        },
    )
    proposals = result.get("structures")
    if not isinstance(proposals, list):
        raise ExtractionError(f"expected a 'structures' list, got keys {sorted(result)}")

    hook_families = {hook.family_id for hook in hooks}
    known_ids = {p.zernio_id for p in posts}
    kept: list[Template] = []
    if len(proposals) > MAX_STRUCTURE_PROPOSALS:
        # Never silently, for the reason `propose_hooks` and `propose_visuals` both record: a
        # truncated batch that reads as a complete one is how a partial answer gets mistaken
        # for the whole picture.
        log.warning(
            "model returned %d structures; considered the first %d",
            len(proposals),
            MAX_STRUCTURE_PROPOSALS,
        )
    for proposal in proposals[:MAX_STRUCTURE_PROPOSALS]:
        try:
            template = _to_structure(
                session, proposal, hook_families, known_ids, cohort, families
            )
        except Exception as exc:  # noqa: BLE001
            # Broad for the same reason as `propose_hooks` above: one unusable proposal
            # costs only itself. The type is logged because the catch is this broad.
            log.warning("structure proposal rejected: %s: %s", type(exc).__name__, exc)
            continue
        kept.append(template)
        # What this batch wrote is visible to the rest of it — the auto-demotion
        # `propose_hooks` documents: two proposals citing one family, and against a snapshot
        # taken before the loop the second writes PROPOSED over the APPROVED the first earned.
        families[template.family_id] = template
    return kept


def compatible_hooks(session: Session, structure: Template) -> list[Template]:
    """The usable hooks this structure declares it pairs with."""
    families = set(structure.body.get("compatible_hook_families") or [])
    if not families:
        return []
    return [h for h in usable_templates(session, TemplateKind.HOOK) if h.family_id in families]
