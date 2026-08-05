"""Revising a candidate against named findings, and stopping.

Blueprint §11 stage 8. Four properties are the whole module, and each one is here because
its opposite is a plausible loop somebody would write:

- **It is bounded by two ceilings this module enforces, not by the model saying it is
  done.** Blueprint §9: every expensive run has a budget. `Budget` refuses the call; a
  `SpendMeter` counts what was bought. Neither is a suggestion the model can argue with,
  because the model is never told either number.
- **It revises against findings and never regenerates.** The prompt is handed the specific
  findings, may return only the fields it changed, and everything it omits is preserved by
  `_merged` character for character. A loop that regenerates is `generation.regenerate_text`
  with extra steps and a bigger bill.
- **It terminates on no progress, not only on success.** See `_progressed` for what progress
  means and why nothing weaker would do.
- **It never claims success it did not verify.** `gates.check` is re-run after every round
  and the result reports what it found — `RevisionResult.outcome` is computed from those
  findings and cannot be set, so a run that stops at a ceiling with findings outstanding
  says `needs_attention` by construction.

**Lineage does not move.** The VISUAL template is passed in and used as given; nothing here
resolves a family to its newest version, and the result and every trace row record the
`(family_id, version)` it was handed. `models/draft.py` and `generation.generated_from` say
at length why the obvious alternative silently credits the wrong template.

**Nothing here orders findings.** They are shown to the model in the order `gates.check`
emitted them, the prompt says that order means nothing, and there is no "worst finding" to
fix first — `gates.Finding` carries no severity by design and this module does not invent
one.

**Scope: hard gates, not the rubric.** `rubric.evaluate` also produces named findings, as
deductions with evidence, and revising against those is the other half of blueprint §11's
`E --findings--> R` arrow. It is deliberately not here: a deduction can only be re-checked
by paying for another rubric call, so a loop targeting deductions either spends a billed
evaluation per round or reports "fixed" for something nobody re-scored. Both are worse than
not doing it yet.

ponytail: gate findings only. Ceiling: a candidate at 70 rubric points with every gate
passing gets nothing from this loop. The upgrade is a re-evaluation callable the caller
supplies — it owns the budget for the extra calls — plus a `Budget` field for them, so the
spend ceiling still covers every call the loop makes.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from sqlmodel import Session

from app import gates
from app.autonomous import SpendMeter
from app.gates import Finding
from app.llm import LLM
from app.models.template import Template
from app.prompts.registry import index
from app.prompts.revision import PROMPTS
from app.prompts.tracing import traced_call

# The exact prompt this module sends, pinned by name and version on one greppable line.
# Resolved out of `PROMPTS` rather than imported as a bare symbol, for the reason
# `app.rubric` states in full: a bare import is a "latest" lookup wearing an import
# statement, and `prompts.get` reads `library.ALL`, whose aggregation is a separate owner's
# edit this wave.
_REVISION = index(PROMPTS)[("revision.targeted", "1.0.0")]

# The most any caller may ask for, whatever it passes. Blueprint §9 wants the ceiling
# enforced server-side, and "server-side" has to mean something stronger than "the default
# is small": `POST /drafts/autonomous-run` shipped a `?cap=500` that bought up to 1001 billed
# completions, which is what a caller-supplied number does when nothing refuses it. A budget
# above these raises at construction, so no request body and no query parameter can reach a
# bigger loop than this file allows.
MAX_ROUNDS_CEILING = 4
MAX_LLM_CALLS_CEILING = 8


class RevisionOutputError(RuntimeError):
    """The model answered, but not with a revision this loop can use.

    Carries what was wrong with both attempts, because it is raised after the one permitted
    repair has already failed and the message is the only thing left to act on. Caught
    inside `revise`, which stops the loop and says so on the result rather than losing the
    rounds that already landed — see there.
    """


@dataclass(frozen=True)
class Budget:
    """Hard ceilings for one revision loop. Counted against attempts, never intentions.

    Two numbers, not one, because a round may cost two calls: the revision and its one
    permitted repair. A loop bounded only by rounds bills twice what its rounds suggest,
    and a loop bounded only by calls has no bound on how many times it re-reads and
    re-checks a candidate it is not improving.

    Shaped after `research.Budget` and separate from `SpendMeter` for its reason: a meter
    answers "what did this cost", a budget answers "may this happen at all", and the second
    question has to be asked before the paid call rather than after it.
    """

    max_rounds: int
    max_llm_calls: int

    def __post_init__(self) -> None:
        # Refused, never clamped. Clamping invents a number — a caller asking for 50 rounds
        # has a bug or a misunderstanding, and silently running 4 hides both. Same reasoning
        # as `rubric.CriterionResult.points_kept` on why deductions past a maximum are
        # rejected rather than trimmed.
        if self.max_rounds < 1 or self.max_rounds > MAX_ROUNDS_CEILING:
            raise ValueError(
                f"max_rounds must be between 1 and {MAX_ROUNDS_CEILING}, got {self.max_rounds}"
            )
        if self.max_llm_calls < 1 or self.max_llm_calls > MAX_LLM_CALLS_CEILING:
            raise ValueError(
                f"max_llm_calls must be between 1 and {MAX_LLM_CALLS_CEILING}, "
                f"got {self.max_llm_calls}"
            )


# What a caller gets without asking. Two rounds, and one spare call for a repair.
#
# Two rather than four: every round is a billed call whose value falls off fast, because a
# revision that did not fix a finding when told exactly what it was is unlikely to fix it
# when told the same thing again — and `_progressed` will stop the loop anyway the moment a
# round stops strictly improving. The ceiling above is what a caller with a reason may raise
# it to; this is the number nobody has to think about.
BUDGET = Budget(max_rounds=2, max_llm_calls=3)


class Outcome(StrEnum):
    """What was established about the candidate, named as narrowly as it was checked.

    `HARD_GATES_PASS` says exactly one thing: `gates.check` was re-run over the returned
    candidate and returned nothing. It is not "good", not "ready" and not "approved" — the
    rubric is a separate pass this loop does not make, and a name suggesting otherwise would
    let a caller treat a gate-clean draft as a reviewed one.
    """

    HARD_GATES_PASS = "hard_gates_pass"
    NEEDS_ATTENTION = "needs_attention"


@dataclass(frozen=True)
class RevisionResult:
    """The candidate as it now stands, what still fails it, and what that cost.

    `findings` is the output of a `gates.check` run over `candidate` itself — not the list
    the loop started with and not what the model claimed to have fixed. That is the only
    reason this result can be trusted at all: everything else in here is bookkeeping, and
    this is the measurement.
    """

    candidate: Mapping[str, Any]

    # What `gates.check` says about `candidate`, in the order the gates emitted them. Not
    # sorted, not grouped and not counted by severity: `Finding` carries no score by design
    # (see its docstring), and a caller wanting "the worst one" is the back door that
    # docstring names.
    findings: tuple[Finding, ...]

    # The template this revision was written against, recorded as the pair that resolves it.
    # Copied off the `Template` the caller passed and never re-resolved, because a revision
    # that moved a draft onto the newest version of the family would credit the wrong
    # template for the words — the defect `CLAUDE.md` says this repo has shipped three times.
    template_family_id: str
    template_version: int

    # Revisions the loop made and re-checked. A round that produced unusable output twice is
    # not one of these — it changed nothing to check — and `llm_calls` is what says it
    # happened.
    rounds: int

    # Billed completions this loop bought, including repair attempts. The **delta** on the
    # caller's meter, not its total: the meter is one object per request and may already
    # carry the generation and evaluation that produced this candidate.
    llm_calls: int

    # Why the loop is not still running, in words. Always set — a result that cannot say why
    # it stopped is one nobody can tell "clean" from "out of budget" on.
    stopped_because: str

    def __post_init__(self) -> None:
        if not self.stopped_because.strip():
            raise ValueError("a revision result must say why the loop stopped")

    @property
    def outcome(self) -> Outcome:
        """Computed from the findings, never stored.

        There is deliberately no field a caller could set to `HARD_GATES_PASS`. A loop that
        stops at its ceiling with findings outstanding, or one that gives up because a round
        fixed nothing, reports `NEEDS_ATTENTION` because the findings are there — not
        because some branch remembered to say so. Same construction as
        `rubric.ReadinessReport.readiness`, and for the same reason.
        """
        return Outcome.NEEDS_ATTENTION if self.findings else Outcome.HARD_GATES_PASS

    @property
    def summary(self) -> str:
        """One line for a person: what remains, what it cost, and why it stopped."""
        remaining = (
            f"{len(self.findings)} hard-gate finding(s) outstanding"
            if self.findings
            else "no hard-gate finding outstanding"
        )
        return (
            f"{remaining} after {self.rounds} revision round(s) and {self.llm_calls} model "
            f"call(s) — {self.stopped_because}."
        )


def revise(
    candidate: Mapping[str, Any],
    *,
    template: Template,
    recent_posts: Sequence[str],
    idea: str,
    llm: LLM,
    session: Session,
    correlation_id: str,
    asset_values: Mapping[str, str] | None,
    unsupported_claims: Sequence[str],
    contradicted_claims: Sequence[str],
    budget: Budget = BUDGET,
    meter: SpendMeter | None = None,
) -> RevisionResult:
    """Revise `candidate` against its hard-gate findings until they are gone or the budget is.

    **The findings are computed here, not taken from the caller.** A caller holds the report
    that sent it this way and could pass its `blocking` list — and that list would be about
    whichever candidate was checked when it was made. Re-deriving them costs nothing
    (`gates.check` is pure, no model and no database) and makes "the findings are about this
    candidate" true by construction rather than by convention.

    `unsupported_claims` and `contradicted_claims` are required keyword arguments with no
    default, unlike on `gates.check` itself. That is deliberate and its docstring asked for
    it: it names the default as the one weak spot in its signature, acceptable while
    generation was the only caller, to be made required "the day a second one appears". This
    is that day, on the door this module owns — a caller that researched and forgets to pass
    the claims would otherwise get a loop that revises a post around the one finding that
    should have stopped it.

    `asset_values` is required too, and may be `None` — an explicit `None` is a statement,
    where an omission is a caller that stopped thinking about it. It matters more here than
    anywhere: the image slots it settles produce `visual_slot_missing` findings, and those
    are the one thing this loop cannot fix, because the model may not write an image URL.
    Such a finding costs exactly one round and then stops the loop on no progress.

    `correlation_id` is minted by the caller with `tracing.new_correlation_id` and shared
    with the generation and evaluation that produced the candidate, so a draft and every
    call spent revising it are one unit in the trace table.

    `meter` is the caller's `SpendMeter` where there is one, so the spend survives a request
    that raises — the arrangement `run_autonomous` and `research.run_research` both document.
    One is made when none is given, so `llm_calls` on the result is always a measurement and
    never a NULL meaning nobody counted.

    Raises `ValueError` via `gates.check` if `template` is not the VISUAL one. It does not
    raise on bad model output: see the loop.
    """
    meter = meter or SpendMeter()
    spent_before = meter.llm_calls
    counted = meter.watch(llm)

    def check(proposed: Mapping[str, Any]) -> tuple[Finding, ...]:
        """Every hard gate over a candidate, with the arguments held identical each round.

        Closed over rather than passed around so no round can be checked against different
        arguments from another — a loop whose later rounds saw a shorter `recent_posts` would
        report a near-duplicate as fixed by the check getting weaker.
        """
        return tuple(
            gates.check(
                proposed,
                template=template,
                recent_posts=recent_posts,
                asset_values=asset_values,
                unsupported_claims=unsupported_claims,
                contradicted_claims=contradicted_claims,
            )
        )

    def spent() -> int:
        return meter.llm_calls - spent_before

    def result(rounds: int, why: str) -> RevisionResult:
        return RevisionResult(
            candidate=candidate,
            findings=findings,
            # Off the template as handed in. `generated_from(session, family, version)`
            # would read the same row today and is still wrong to write here: it is a lookup
            # that a later edit can widen to "newest in the family", which is exactly how
            # `regenerate_visual` came to store a v3 picture under `visual_version: 2`.
            template_family_id=template.family_id,
            template_version=template.version,
            rounds=rounds,
            llm_calls=spent(),
            stopped_because=why,
        )

    findings = check(candidate)
    if not findings:
        # Checked before spending anything. A caller that hands over a clean candidate — an
        # autonomous run that gates everything it writes, say — pays nothing, and the result
        # still says `HARD_GATES_PASS` on a gate run rather than on an assumption.
        return result(0, "the candidate has no hard-gate finding, so there was nothing to fix")

    rounds = 0
    while findings:
        if rounds >= budget.max_rounds:
            return result(
                rounds,
                f"the iteration ceiling of {budget.max_rounds} round(s) was reached with "
                f"{len(findings)} finding(s) outstanding",
            )
        remaining = budget.max_llm_calls - spent()
        if remaining < 1:
            return result(
                rounds,
                f"the spend ceiling of {budget.max_llm_calls} model call(s) was reached with "
                f"{len(findings)} finding(s) outstanding",
            )

        try:
            answer = _one_revision(
                candidate,
                findings=findings,
                template=template,
                idea=idea,
                llm=counted,
                session=session,
                correlation_id=correlation_id,
                round_number=rounds + 1,
                # The repair is a second billed call inside the same round, so it is subject
                # to the same ceiling. Without this term a budget of one call buys two.
                may_repair=remaining >= 2,
            )
        except RevisionOutputError as exc:
            # Stopped, not raised. The rounds that already landed are real revisions this
            # candidate keeps, and raising here would throw them away to report a failure the
            # result can carry perfectly well: `outcome` is `NEEDS_ATTENTION` because the
            # findings are still there, and this sentence says why nothing more was tried.
            # Blueprint §11 asks for an actionable failure after the one repair, and an
            # actionable failure is what a caller reads off `stopped_because` here.
            return result(rounds, f"the model returned output this loop cannot use: {exc}")

        rounds += 1
        proposed = _merged(candidate, answer)
        after = check(proposed)

        if not _progressed(before=findings, after=after):
            # **The round is discarded**, and the candidate that made the last real
            # improvement is what comes back. A revision that traded one finding for another
            # is not an improvement, and handing it back would let a caller persist a draft
            # whose new finding arrived from this loop.
            return result(
                rounds,
                "the last round fixed nothing it did not also break, so it bought no further "
                f"round; {len(findings)} finding(s) outstanding",
            )

        candidate, findings = proposed, after

    return result(rounds, "every hard-gate finding was fixed, confirmed by re-running the gates")


def _progressed(*, before: tuple[Finding, ...], after: tuple[Finding, ...]) -> bool:
    """Did this round strictly improve the candidate?

    **Progress is: every finding that remains was already there, and at least one is gone.**
    A proper subset, and nothing weaker, because each weaker rule is a loop that runs
    forever on a model that is not helping:

    - "anything changed" lets a revision trade `length` for `forbidden_claim` and back
      again, buying a round each time.
    - "fewer findings than before" is arithmetic over findings, which needs findings to have
      a magnitude. They do not: `gates.Finding` carries no score by design, and inventing
      one here — even as a count — is how a review screen ends up showing the worst problems
      first.

    A finding whose *detail* changed is a different finding, so a post that is still too long
    by fewer characters has not progressed. That reads harsh and it is the same rule: calling
    "3,100 characters" an improvement on "3,200 characters" is a comparison between two
    findings, and this module is not allowed to make one. The loop stops, the result says
    the length finding is outstanding, and a person sees a draft that is honestly still too
    long instead of a loop shaving fifty characters a round.

    Sets rather than sequences, so the order the gates emitted them in — which is not
    meaningful — cannot make an unchanged candidate look changed. Two identical findings
    collapse into one, which costs nothing: identical `(gate, detail)` pairs are the same
    statement about the same draft.
    """
    return set(after) < set(before)


def _merged(previous: Mapping[str, Any], answer: Mapping[str, Any]) -> dict[str, Any]:
    """The candidate with the model's changes over it — and everything else untouched.

    This is what makes "return only what you changed" safe to ask for, and it is the
    structural half of *preserve what was already right*: an omitted `body` is not an empty
    body, it is the body that was already there, byte for byte. A model that answered with
    the whole draft every time would still work; a model that answered with only the hook
    would, without this, silently blank the rest.

    `visual_values` merges per slot for the same reason one level down. A revision fixing one
    slot's wording must not drop the six slots it did not mention — the render would then
    fail on `MissingSlotValue`, or worse, `gates._visual_slots` would report six new findings
    and `_progressed` would blame the model for something this function did.

    **Nothing is coerced.** `str(answer["hook"])` here would turn a model's `5` into the
    string `"5"` and hide a schema violation that `gates._schema` exists to report — and
    `_answer` has already refused a non-string, so anything arriving here is the right type
    or the caller wanted the finding.
    """
    base = dict(previous) if isinstance(previous, Mapping) else {}
    revised: dict[str, Any] = dict(base)

    for key in ("hook", "body"):
        if key in answer:
            revised[key] = answer[key]

    if "visual_values" in answer:
        existing = base.get("visual_values")
        kept = dict(existing) if isinstance(existing, Mapping) else {}
        revised["visual_values"] = {**kept, **answer["visual_values"]}

    return revised


def _one_revision(
    candidate: Mapping[str, Any],
    *,
    findings: tuple[Finding, ...],
    template: Template,
    idea: str,
    llm: LLM,
    session: Session,
    correlation_id: str,
    round_number: int,
    may_repair: bool,
) -> dict[str, Any]:
    """One revision call, with the one repair blueprint §11 permits — and no loop.

    Written as a second statement rather than a retry loop, exactly as `rubric.evaluate`
    writes it: the way a "max 2 attempts" loop becomes "max 5" is somebody editing a
    constant, and there is no constant here. A third attempt means adding a call, which is
    visible in review.

    Raises `RevisionOutputError` naming both failures when the repair fails too, or naming
    the first when the budget had no room for a repair.
    """
    message = _user_message(candidate, findings=findings, template=template, idea=idea)
    artifacts: dict[str, Any] = {
        # `(family_id, version)`, never the row id and never "latest" — a trace attributed to
        # the wrong template version is the defect this repo has shipped three times.
        "template_family_id": template.family_id,
        "template_version": template.version,
        "revision_round": round_number,
    }

    answer = traced_call(
        session,
        llm,
        _REVISION,
        message,
        correlation_id=correlation_id,
        input_artifact_ids=artifacts,
    )
    try:
        return _answer(answer)
    except RevisionOutputError as first:
        if not may_repair:
            raise RevisionOutputError(
                f"{first}. The spend ceiling left no room for the one repair attempt"
            ) from first
        # `first` goes into the message outside the fence, and what makes that safe is the
        # `!r` on every model-supplied value in `_answer`'s messages: `repr` escapes the
        # newlines a model would need to draw a second instruction block in here.
        answer = traced_call(
            session,
            llm,
            _REVISION,
            f"{message}\n\n{_REPAIR_PREFIX}\n{first}",
            correlation_id=correlation_id,
            input_artifact_ids={**artifacts, "repair": True},
        )
        try:
            return _answer(answer)
        except RevisionOutputError as second:
            raise RevisionOutputError(
                f"before and after its one repair attempt. First: {first}. "
                f"After repair: {second}"
            ) from second


# Prefixed to the original message on the one repair attempt. The original message is
# repeated in full rather than sending the complaint alone: the model has no memory of the
# first call, and a bare "your answer carried no fields" is not a task.
_REPAIR_PREFIX = (
    "Your previous answer could not be used. Return the same JSON shape, fixing exactly this "
    "and changing nothing else about the revision you made:"
)

# The three keys a revision may carry. Anything else is dropped rather than refused, the way
# `generation._written_values` drops a slot the model may not write: a model volunteering
# `"notes"` has not failed the task, and spending the one repair attempt on it would.
_FIELDS = ("hook", "body", "visual_values")


def _answer(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the revision into the fields it changed, or say precisely what is wrong.

    Every rejection names the field, because that message is what gets sent back on the
    repair attempt and then, if that fails too, what a person reads.

    An empty answer is refused rather than treated as "nothing needed changing". The model
    was handed findings; a reply carrying none of the three fields addressed none of them,
    and counting it as a round that changed nothing would spend the round *and* report no
    progress, which blames the loop for the model's answer.
    """
    if not isinstance(raw, Mapping):
        raise RevisionOutputError(f"the answer must be an object, got {type(raw).__name__}")

    changed = {key: raw[key] for key in _FIELDS if key in raw}
    if not changed:
        raise RevisionOutputError(
            "the answer carries none of 'hook', 'body' or 'visual_values', so it revises "
            "nothing; return the fields you changed"
        )

    for key in ("hook", "body"):
        if key in changed and not isinstance(changed[key], str):
            raise RevisionOutputError(
                f"{key!r} must be a string, got {type(changed[key]).__name__}"
            )

    values = changed.get("visual_values")
    if values is not None:
        if not isinstance(values, Mapping):
            raise RevisionOutputError(
                f"'visual_values' must be an object, got {type(values).__name__}"
            )
        for name, value in values.items():
            # `bool` excluded explicitly because it subclasses `int`: without that term
            # `True` passes as a number, fills the slot with the word "True", and both
            # `gates._schema` and the completeness gate agree the slot is filled. The same
            # trap `gates._schema` and `rubric.Deduction` each document.
            if not isinstance(value, str | int | float) or isinstance(value, bool):
                raise RevisionOutputError(
                    f"visual_values[{name!r}] must be text or a number, got "
                    f"{type(value).__name__}"
                )

    return changed


# --- what the model is shown ----------------------------------------------------------------

# The fence, same construction as `rubric._OPEN` and `research._OPEN`: a marker that could
# plausibly occur in prose is not a marker. Restated rather than imported — `rubric`'s are
# private, and importing `app.rubric` for four characters would put every revision behind the
# rubric prompt resolving at import.
_OPEN = "<<<PIXII-{label}>>>"
_CLOSE = "<<<END PIXII-{label}>>>"

# Rewritten wherever they occur inside a block. `<<<` and `>>>` are the only sequences a
# marker is built from, so text inside a block can neither close it nor open another.
_FORGERY = (("<<<", "‹‹‹"), (">>>", "›››"))


def _fenced(label: str, text: str) -> str:
    """One block of quoted material, unable to address the model from inside itself."""
    for forged, replacement in _FORGERY:
        text = text.replace(forged, replacement)
    return f"{_OPEN.format(label=label)}\n{text}\n{_CLOSE.format(label=label)}"


def _user_message(
    candidate: Mapping[str, Any],
    *,
    findings: tuple[Finding, ...],
    template: Template,
    idea: str,
) -> str:
    """The draft as it stands, its slot text, the idea, and every finding by name.

    **The hook and the body are shown as separate blocks**, where `rubric._user_message`
    joins them into the string that would publish. The difference is the task: the evaluator
    judges the post a reader sees, and this model has to return one field or the other, so a
    joined block would leave it guessing where the hook ended.

    The findings are fenced too, and not only the draft. A finding's detail quotes an excerpt
    of a recent post, or a claim text a fetched page put into a dossier — blueprint invariant
    7 does not stop applying because the text arrived inside a `Finding`.
    """
    data: Mapping[str, Any] = candidate if isinstance(candidate, Mapping) else {}
    # Coerced for display exactly as `gates.check` coerces before measuring, so the model is
    # shown the string the gates judged rather than a prettier one.
    hook = str(data.get("hook") or "").strip()
    body = str(data.get("body") or "").strip()
    supplied = data.get("visual_values")
    written: Mapping[str, Any] = supplied if isinstance(supplied, Mapping) else {}

    numbered = "\n".join(
        f"{position}. [{finding.gate}] {finding.detail}"
        for position, finding in enumerate(findings, 1)
    )

    parts = [
        f"Idea the draft was written from: {idea.strip() or '(none supplied)'}",
        f"Visual template: {template.name}",
        "The draft's opening:",
        _fenced("DRAFT-HOOK", hook),
        "The rest of the draft:",
        _fenced("DRAFT-BODY", body),
    ]
    if written:
        slots = "\n".join(f"{name}: {value}" for name, value in written.items())
        parts.append("The text rendered into the post's picture:")
        parts.append(_fenced("VISUAL-SLOT-TEXT", slots))
    parts.append(
        # Said at the point of use as well as in the system prompt. The numbering is the one
        # thing in this message that could be read as a ranking, and `gates.Finding` carries
        # no severity precisely so that nothing downstream can claim one.
        "Everything found wrong with this draft, numbered only so you can tell them apart. "
        "The order is the order the checks ran in and says nothing about which matters more. "
        "Address all of them:"
    )
    parts.append(_fenced("FINDINGS", numbered))
    return "\n\n".join(parts)


__all__ = [
    "BUDGET",
    "MAX_LLM_CALLS_CEILING",
    "MAX_ROUNDS_CEILING",
    "Budget",
    "Outcome",
    "RevisionOutputError",
    "RevisionResult",
    "revise",
]
