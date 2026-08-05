"""The two stages that stand between an idea and a draft: the brief, then the angle and claim
plan.

The one-shot writing prompt asked a model to decide what a post was for, what it would argue
and how it would say it, all inside the call that wrote the words — so none of those decisions
was inspectable, and none of them could be checked against anything. This module makes the
first two into artifacts. Three rules shape it:

- **A claim plan is a list of claims, not a paragraph about them.** Each planned claim is one
  addressable row, because the next slice has to say "this claim is supported by that
  citation" and that sentence needs an id on both sides. A model answering with prose is
  refused rather than stored: `UnplannableClaims` is raised, and the plan is not written.
- **Research depth is `research.resolve_mode`'s answer and nothing else.** Nothing here
  compares modes, clamps one, or has an opinion about which briefs need looking things up.
  What this module *does* decide is which text constitutes the question — see
  `research_question`, which is a different thing and is documented where it happens.
- **Nothing here ranks.** One thesis per plan, one row per claim, no scores, no alternatives,
  and no column that could hold a comparison. `AnglePlan` says why at its `thesis` field.

These stages sit behind `generation`'s interface rather than replacing it. Wiring them into
`generate_draft` is a separate edit to a file this slice does not own; the pair is usable
today as `build_brief` then `plan_angle`, and the question the research stage should ask is
`research_question(brief.idea, [c.text for c in planned_claims(session, plan)])`.
"""

import logging
import re
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlmodel import Session, col, select

from app import research
from app.llm import LLM
from app.models.editorial import CHANNEL, AnglePlan, EditorialBrief, PlannedClaim
from app.output_schema import validate as validate_output
from app.prompts.editorial import ANGLE_PLAN, BRIEF
from app.prompts.tracing import new_correlation_id, traced_call

log = logging.getLogger("pixii.editorial")

# What one planned claim may be. Both bounds exist to keep "individually addressable" true,
# and the length one is the load-bearing half: a model handed "list the claims" will sometimes
# answer with a single entry holding a paragraph, which satisfies every structural check —
# a dict, with a `text` key, holding a non-empty string — and is exactly the prose blob this
# artifact exists to refuse. A later slice would then have one "claim" it cannot map to a
# citation, and nothing would have complained.
#
# The minimum is the mirror: "yes" is a string, and it is not a statement anybody can check.
#
# ponytail: character and terminator counting, not a parse. A real check of "is this one
# assertion" needs to read the sentence, which is another billed call and one the plan prompt
# could talk out of its own bound. Ceiling: these numbers reject a paragraph and accept a
# sentence, and nothing more. Upgrade path is the hard-gates slice, where a claim that survives
# here can be checked against what the draft actually asserts.
CLAIM_MIN_CHARS = 12
CLAIM_MAX_CHARS = 240
# Two, not one, because a sentence terminator is not a sentence: "U.S. holdings rose." carries
# two matches and is one claim. Two tolerates the common abbreviation and still refuses the
# three-sentence argument that is really a paragraph.
CLAIM_MAX_SENTENCES = 2

# A terminator only counts when something follows it, or it ends the string. `.` inside "U.S"
# is followed by a letter and is not the end of anything.
_SENTENCE_END = re.compile(r"[.!?](?:\s|$)")


class UnplannableClaims(ValueError):
    """The plan came back with nothing that could be addressed as a claim.

    Raised, and the plan is not written. This is the one failure in this module that is not
    survivable: a plan whose claims cannot be addressed one at a time is a plan that makes
    research uncheckable, and storing it would hand the next slice a paragraph where it
    expects rows. A run that hits this has a prompt or a model problem, and it should say so
    at the stage that noticed rather than three stages later.
    """


def research_question(idea: str, claims: Sequence[str] = ()) -> str:
    """The text `research.resolve_mode` and `research.run_research` are asked about.

    **This function chooses what the question is. It does not choose what depth follows from
    it** — that is `resolve_mode`, in full, and the distinction is the whole reason this is a
    separate function rather than a line inside `build_brief`.

    Two details are load-bearing, and both were established by running the detector rather than
    by reading it:

    Every line is bulleted. `research._names_a_thing` ignores a capitalised word at the start
    of a sentence, so a claim written "Microsoft shipped a new tier" trips no signal when it
    is the first thing on its line, and trips `organisation` when `- ` precedes it. Joining
    claims into one paragraph with `". "` is the worst of both: it puts *every* claim's first
    word at a sentence start and hides all of them. The bullet is what makes the floor see a
    claim that names a company.

    Claims are appended and the idea stays first, so the text only ever grows at the end. That
    is what makes the floor monotone — adding claims can add signals and can never remove one
    — and `plan_angle` relies on it to re-resolve the depth upwards without ever lowering it.

    The brief's other fields are deliberately not here. `channel` is "LinkedIn", a capitalised
    name that would trip `organisation` on every brief ever written, and `audience` and
    `constraints` are full of segment names and word counts that describe the *shape* of the
    post rather than anything a reader could check. Feeding them in would pin every brief to
    `light` and make the floor a constant, which is a floor that has stopped saying anything.
    """
    return "\n".join(f"- {line}" for line in [idea, *claims])


def build_brief(
    session: Session,
    llm: LLM,
    *,
    idea: str,
    requested_mode: str | None = None,
    proposed_time: datetime | None = None,
    channel: str = CHANNEL,
    correlation_id: str | None = None,
) -> EditorialBrief:
    """Turn an idea into a brief: what it is for, who for, and how much research it needs.

    `requested_mode` is the operator's preference and defaults to `None`, meaning none was
    expressed. It is passed straight to `research.resolve_mode`, which is where a request below
    the floor is refused — this function has no opinion about which briefs need research, and
    a second opinion here would be worse than none. A `ModeBelowFloor` therefore propagates
    unchanged, and no brief row is written: a brief recording a depth its own floor forbids is
    not a thing that should exist to be read later.

    ponytail: `session.add` and no commit, like `traced_call` and `run_research`. The brief
    lands or rolls back with the caller's work.
    """
    # Resolved before the model is called, so a request below the floor costs nothing. The
    # question is the idea alone at this point; `plan_angle` asks again once there are claims,
    # which can only raise it.
    resolved = research.resolve_mode(research_question(idea), requested_mode)

    # Minted once. Two calls to `new_correlation_id` would put the trace row and the artifact
    # it describes under different ids, which is the one thing a correlation id may not do.
    correlation = correlation_id or new_correlation_id()

    answer = traced_call(
        session,
        llm,
        BRIEF,
        f"Idea:\n{idea}\n\nChannel: {channel}",
        correlation_id=correlation,
        # No input artifact: the input to this stage is the operator's own sentence, which has
        # no id. The correlation id is what ties this call to everything downstream of it.
    )
    validate_output(answer, BRIEF.output_schema)

    brief = EditorialBrief(
        correlation_id=correlation,
        idea=idea,
        objective=_text(answer, "objective"),
        audience=_text(answer, "audience"),
        channel=channel,
        desired_action=_text(answer, "desired_action"),
        constraints=_lines(answer.get("constraints")),
        # The operator's preference as given, including `None` for "none was expressed".
        # Storing `resolved.mode` here instead would lose the difference between a brief that
        # asked for `deep` and a brief whose floor happened to land there.
        requested_mode=requested_mode,
        recommended_mode=resolved.recommended,
        research_mode=resolved.mode,
        mode_signals=list(resolved.signals),
        proposed_time=proposed_time,
        # The prompt that wrote the words above, on the row that holds them.
        prompt_name=BRIEF.name,
        prompt_version=BRIEF.version,
    )
    session.add(brief)
    session.flush()
    return brief


def plan_angle(
    session: Session,
    llm: LLM,
    brief: EditorialBrief,
    *,
    recent_topics: Sequence[str] = (),
    correlation_id: str | None = None,
) -> AnglePlan:
    """Plan what the post argues and what it will assert, and re-settle the research depth.

    Two things happen here that are not obvious from the signature.

    **The claims decide the depth, not only the idea.** A brief reading "why our onboarding
    changed" trips no floor signal; a plan that intends to assert "Stripe cut activation time
    by a third in 2025" plainly does. So the depth is resolved again over the idea *and* the
    claims, through the same `research.resolve_mode`, and written back to the brief. If the
    operator asked for a depth the claims have now outgrown, `ModeBelowFloor` propagates and
    the plan is not written — the same refusal `build_brief` makes, at the stage that learned
    the new fact.

    **Every claim already planned for this brief is in that question, not merely this plan's.**
    A brief may have more than one plan, and re-planning must not be able to lower its depth:
    plan 1 intending to assert a company fact, followed by a blander plan 2, would otherwise
    re-resolve the brief back to `none` while plan 1's rows — and any draft written from them —
    still exist. That is the silent downgrade blueprint §12 forbids, reached through this
    module's own front door. Including the earlier claims makes the question grow across
    re-plans exactly as it grows within one, which is what makes "this can only move upwards"
    true rather than merely intended.

    **The plan is written after the depth is settled**, for that reason: a stored plan whose
    brief still claims a depth its own claims forbid is a row that reads as fine and is not.
    """
    correlation = correlation_id or brief.correlation_id

    answer = traced_call(
        session,
        llm,
        ANGLE_PLAN,
        _plan_prompt(brief, recent_topics),
        correlation_id=correlation,
        # The brief is a real artifact with a real id, so this trace names it. `build_brief`'s
        # call has nothing to name and passes none.
        input_artifact_ids={"editorial_brief": brief.id},
    )
    claims = _planned(answer.get("claims"))
    if not claims:
        raise UnplannableClaims(
            "the angle plan named no claim that could be addressed on its own; "
            "a claim plan is a list of statements, not a paragraph about them"
        )
    validate_output(answer, ANGLE_PLAN.output_schema)

    # Before the plan row, deliberately — see the docstring. A `ModeBelowFloor` from here
    # leaves no plan behind.
    resolved = research.resolve_mode(
        research_question(brief.idea, [*_claims_so_far(session, brief), *claims]),
        brief.requested_mode,
    )
    brief.recommended_mode = resolved.recommended
    brief.research_mode = resolved.mode
    brief.mode_signals = list(resolved.signals)
    session.add(brief)

    plan = AnglePlan(
        brief_id=_brief_id(brief),
        correlation_id=correlation,
        thesis=_text(answer, "thesis"),
        tension=_text(answer, "tension"),
        audience_stake=_text(answer, "audience_stake"),
        cta=_text(answer, "cta"),
        beats=_lines(answer.get("beats")),
        # The caller's list, not the model's echo of it. A model handing the list back can
        # drop an entry, and the failure would be a repeat post nobody could account for.
        must_not_repeat=[topic.strip() for topic in recent_topics if topic.strip()],
        prompt_name=ANGLE_PLAN.name,
        prompt_version=ANGLE_PLAN.version,
    )
    session.add(plan)
    # Flushed because every claim needs the plan's id, and the id comes from the database.
    session.flush()

    for text in claims:
        session.add(PlannedClaim(plan_id=_plan_id(plan), text=text))
    session.flush()
    return plan


def planned_claims(session: Session, plan: AnglePlan) -> list[PlannedClaim]:
    """This plan's claims, in the order the plan stated them.

    Ordered by id, which is insertion order, which is the order the model wrote them in — and
    **by nothing else**. There is no ordering here by length, by how checkable a claim looks or
    by anything a reader could mistake for importance; a list presented in an order implies the
    order means something, which is the reasoning `research.dossier` sets out and the reason
    this project does not rank.
    """
    statement = (
        select(PlannedClaim)
        .where(PlannedClaim.plan_id == plan.id)
        .order_by(col(PlannedClaim.id))
    )
    return list(session.exec(statement).all())


def _claims_so_far(session: Session, brief: EditorialBrief) -> list[str]:
    """Every claim already planned for this brief, across all of its plans.

    Ordered by id — insertion order — so that appending this plan's claims to it produces a
    question that only ever grows. Ordering by anything else would put an earlier claim after
    a later one and quietly break the monotonicity the caller depends on.
    """
    statement = (
        select(PlannedClaim.text)
        .join(AnglePlan, col(PlannedClaim.plan_id) == AnglePlan.id)
        .where(AnglePlan.brief_id == brief.id)
        .order_by(col(PlannedClaim.id))
    )
    return list(session.exec(statement).all())


def _plan_prompt(brief: EditorialBrief, recent_topics: Sequence[str]) -> str:
    parts = [
        f"Idea:\n{brief.idea}",
        f"\nObjective: {brief.objective}",
        f"Audience: {brief.audience}",
        f"Desired reader action: {brief.desired_action}",
    ]
    if brief.constraints:
        parts.append("\nConstraints:\n" + "\n".join(f"- {c}" for c in brief.constraints))
    topics = [topic.strip() for topic in recent_topics if topic.strip()]
    if topics:
        # Named as subjects already covered rather than as forbidden words: the rule is about
        # not writing the same post twice, and a model told to avoid a *phrase* will write the
        # same post around the phrase.
        parts.append(
            "\nSubjects this account has covered recently. Do not plan a post that makes the"
            " same argument as any of them:\n" + "\n".join(f"- {topic}" for topic in topics)
        )
    return "\n".join(parts)


def _planned(raw: Any) -> list[str]:
    """The claim texts that are addressable on their own, in the order they arrived.

    Malformed entries are dropped with a log line and the rest of the plan survives; a plan
    with nothing left is refused by the caller. The asymmetry is deliberate and it is the
    direction that is safe: a dropped claim is a claim the post will not be asked to make, so
    it removes an assertion rather than smuggling an unsourced one through. Reading a paragraph
    charitably as one claim does the opposite — it produces a "claim" nothing can be mapped to,
    and the draft asserts all of it anyway.
    """
    if not isinstance(raw, list):
        # Includes the case this bound exists for: `"claims": "a paragraph about the post"`.
        # Splitting a string into claims here would be this module inventing the structure the
        # prompt asked the model for.
        log.warning("angle plan: claims came back as %s, not a list", type(raw).__name__)
        return []

    texts: list[str] = []
    for item in raw:
        if not isinstance(item, dict):
            log.warning("angle plan: dropped a claim that was not an object")
            continue
        text = str(item.get("text", "")).strip()
        if not _addressable(text):
            log.warning("angle plan: dropped a claim that is not one statement: %.60r", text)
            continue
        texts.append(text)
    return texts


def _addressable(text: str) -> bool:
    """Whether this is one statement somebody could check without reading the others."""
    if not CLAIM_MIN_CHARS <= len(text) <= CLAIM_MAX_CHARS:
        return False
    return len(_SENTENCE_END.findall(text)) <= CLAIM_MAX_SENTENCES


def _text(answer: dict, key: str) -> str:
    return str(answer.get(key) or "").strip()


def _lines(raw: Any) -> list[str]:
    """A list of non-empty strings, or `[]` — never `None`.

    `[]` is the honest answer here: the pass ran and named nothing. The columns these feed are
    not nullable for exactly that reason.
    """
    if not isinstance(raw, list):
        return []
    return [str(item).strip() for item in raw if str(item).strip()]


# `id` is `int | None` on every model in this schema — the column is assigned by the database —
# and these say "flushed, therefore assigned" once rather than at each use.
def _brief_id(brief: EditorialBrief) -> int:
    if brief.id is None:
        raise RuntimeError("editorial brief has no id; it has not been flushed")
    return brief.id


def _plan_id(plan: AnglePlan) -> int:
    if plan.id is None:
        raise RuntimeError("angle plan has no id; it has not been flushed")
    return plan.id


__all__ = [
    "CLAIM_MAX_CHARS",
    "CLAIM_MAX_SENTENCES",
    "CLAIM_MIN_CHARS",
    "AnglePlan",
    "EditorialBrief",
    "PlannedClaim",
    "UnplannableClaims",
    "build_brief",
    "plan_angle",
    "planned_claims",
    "research_question",
]
