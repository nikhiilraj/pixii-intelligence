"""The editorial-readiness rubric: what a candidate loses points for, and on what evidence.

**A readiness report says one thing: whether a draft is ready for a human editor to read.**
It never says the draft will do well. Blueprint invariant 6 — "quality is not performance
prediction" — is the reason this module exists at all, and the reason its vocabulary is what
it is. There is no `score`, no `rating`, no `confidence` and no `predicted_` anything here.
`readiness_points` is a count of rubric compliance, `Readiness` has two values and both of
them are statements about *review*, and `ReadinessReport` is deliberately not orderable and
not convertible to a number, so a caller cannot sort candidates by it or hand it to a chart
axis labelled "expected engagement" without writing the coercion themselves.

**It scores one candidate. It does not rank templates.** `evaluate` takes a single
`candidate`, never a candidate set, and that is a decision rather than an omission: an
`evaluate(candidate_set)` returning a list of reports is one `max()` away from being a
ranking, and the `CLAUDE.md` rule it would break — ~3 samples per template across a 12.7×
engagement spread — is not a rule about intent. Nothing here aggregates by template, and
there is no function taking a collection of reports.

**A model's self-score is not a release gate.** Two things stop it being one. The model is
never asked for a score: it returns deductions, each naming a criterion, a cost and quoted
evidence, and the arithmetic happens here where it can be checked. And every report carries
`gates.check`'s findings, with a hard-gate failure disqualifying the candidate at any point
total — see `ReadinessReport.readiness`.

**The rubric is versioned data.** `RUBRIC_VERSION` moves whenever `CRITERIA` does, every
report records the version that produced it alongside the prompt that scored it, and the
weights are pinned by a literal and a digest in `tests/test_rubric.py`. Same reasoning as
prompt versions and template lineage: a number whose rubric is unknown compares to nothing.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from sqlmodel import Session

from app import gates
from app.gates import Finding
from app.llm import LLM
from app.models.template import Template
from app.prompts.registry import index
from app.prompts.rubric import PROMPTS
from app.prompts.tracing import traced_call

# The exact prompt this module sends, pinned by name and version on one greppable line.
#
# Resolved out of `PROMPTS` rather than imported as a bare symbol, and not through
# `prompts.get` either. A bare `from app.prompts.rubric import EDITORIAL_READINESS` is a
# "latest" lookup wearing an import statement: bump that prompt to 1.1.0 and this module
# silently starts sending different words, which is the failure `prompts.get`'s docstring
# is written against. `prompts.get` would say the version out loud too, but it reads
# `library.ALL`, and the aggregation into `ALL` is a separate owner's edit this wave —
# depending on it would make this module fail at import until that edit lands. Indexing
# `PROMPTS` gives the same object the registry will hold, with a `KeyError` at import the
# moment the version moves.
_READINESS = index(PROMPTS)[("evaluation.editorial_readiness", "1.0.0")]

# MAJOR.MINOR.PATCH, the same shape `prompts.registry` demands of a prompt version, and for
# the same reason: `"1.0"` and `"1.0.0"` on two reports would name one rubric while sorting
# and comparing as two.
_SEMVER = re.compile(r"^\d+\.\d+\.\d+$")

# The rubric's own version, recorded on every report it produces.
#
# **Changing any weight below is a new version here.** Not a nicety: two reports carrying
# `1.0.0` and disagreeing about what 90 points means are two numbers that cannot be put in
# the same sentence, and nothing downstream would be able to tell. The tests pin both the
# weights and this string, so a weight edited without a version bump fails.
RUBRIC_VERSION = "1.0.0"

# What a clean candidate is worth. Asserted against `CRITERIA` at import — see below.
TOTAL_POINTS = 100

# Blueprint §11: "An **A** means at least 90 points and every hard gate passed." The
# blueprint's letter is kept here as a constant name so its language stays greppable; the
# verdict itself is `Readiness.READY_FOR_EDITORIAL_REVIEW`, because a letter grade is the
# one shape of this number a reader would most readily mistake for a quality ranking.
A_THRESHOLD_POINTS = 90

# Said in full on every report, because the sentence a number needs attached to it is the
# one nobody writes down.
DISCLAIMER = (
    "Editorial readiness is compliance with a fixed rubric. It is not a prediction of "
    "engagement, and it does not rank templates against each other."
)


@dataclass(frozen=True)
class Criterion:
    """One line of the rubric: what it asks, and the most it can cost."""

    key: str  # stable, machine-readable; what a `Deduction` names
    title: str  # what a person reads on a review screen
    max_points: int


# The rubric, as data. Weights are the blueprint's §11 suggestion, unchanged.
#
# The order is the order a report renders in, and it is the blueprint's order rather than
# descending weight — sorting the rubric by weight would put "evidence and claim support"
# first for arithmetic reasons and imply the list is a priority ordering, which it is not.
CRITERIA: tuple[Criterion, ...] = (
    Criterion("hook_clarity", "Hook clarity and tension", 15),
    Criterion("specificity", "Specificity and information density", 15),
    Criterion("evidence_support", "Evidence and claim support", 20),
    Criterion("structure_flow", "Narrative structure and flow", 15),
    Criterion("voice_fidelity", "Voice fidelity", 15),
    Criterion("originality", "Originality against the recent corpus", 10),
    Criterion("reader_value", "Reader value and CTA coherence", 10),
)

_BY_KEY: dict[str, Criterion] = {criterion.key: criterion for criterion in CRITERIA}

if not _SEMVER.match(RUBRIC_VERSION):  # pragma: no cover — import-time invariant
    raise ValueError(f"RUBRIC_VERSION {RUBRIC_VERSION!r} is not MAJOR.MINOR.PATCH")

if len(_BY_KEY) != len(CRITERIA):  # pragma: no cover — import-time invariant
    raise ValueError("two criteria share a key; one of them would be unreachable")

if sum(criterion.max_points for criterion in CRITERIA) != TOTAL_POINTS:
    # Checked at import rather than in a test alone. `A_THRESHOLD_POINTS` is 90 *out of a
    # hundred*; a table summing to 95 makes the threshold mean something nobody chose, and
    # every report written before anyone noticed would carry a number that cannot be
    # reinterpreted after the fact.
    raise ValueError(  # pragma: no cover — import-time invariant
        f"the criteria sum to {sum(c.max_points for c in CRITERIA)}, not {TOTAL_POINTS}"
    )


class Readiness(StrEnum):
    """The verdict, and there are two of them.

    Both name a state of *review*, not a state of the market. There is deliberately no
    `GOOD`, `STRONG` or `HIGH_QUALITY` member and no letter grade: the blueprint's "A" is
    `READY_FOR_EDITORIAL_REVIEW`, spelled out, because "A" invites the question "what did
    this template average" and this one does not.
    """

    READY_FOR_EDITORIAL_REVIEW = "ready_for_editorial_review"
    NEEDS_REVISION = "needs_revision"


class RubricOutputError(RuntimeError):
    """The evaluator answered, but not with deductions this rubric can use.

    Carries what was wrong — which deduction, which field — rather than "invalid output",
    because it is raised after the one permitted repair attempt has already failed and the
    message is the only thing left to act on.
    """


@dataclass(frozen=True)
class Deduction:
    """Points off one criterion, with the quoted evidence that justifies them.

    **A deduction without evidence cannot be constructed.** That is enforced here, in
    `__post_init__`, rather than asked for in the prompt or checked by the caller: a score
    with no reason behind it is unactionable for the writer and unauditable for whoever
    asks, six weeks later, why those five points went. The prompt asks for evidence too —
    but a prompt is a request and this is the thing that makes it true.

    `points` is what the criterion *loses*. There is no field for points awarded anywhere
    in this module, and that asymmetry is the point: nothing the model returns is a verdict
    on the draft, only a list of specific complaints, so no number here originated as a
    model's opinion of quality.
    """

    criterion: str  # a key in `CRITERIA`
    points: int  # > 0, and never more than that criterion's maximum
    reason: str  # what is wrong, specifically enough to fix
    evidence: str  # words from the candidate that show it

    def __post_init__(self) -> None:
        if self.criterion not in _BY_KEY:
            # `!r` on the criterion for the reason `_whole` gives: this sentence is quoted
            # back to the model on the repair attempt, and it is the model's own string.
            raise ValueError(
                f"unknown criterion {self.criterion!r}; the rubric has {sorted(_BY_KEY)}"
            )
        limit = _BY_KEY[self.criterion].max_points
        # `bool` is excluded explicitly because it subclasses `int` — without that term
        # `True` passes as one point and the deduction reads as real. The same trap
        # `gates._schema` documents for slot values.
        if isinstance(self.points, bool) or not isinstance(self.points, int):
            raise ValueError(
                f"{self.criterion}: points must be a whole number, got {type(self.points).__name__}"
            )
        if self.points < 1:
            raise ValueError(
                f"{self.criterion}: a deduction of {self.points} points deducts nothing; "
                "say nothing instead"
            )
        if self.points > limit:
            raise ValueError(
                f"{self.criterion}: {self.points} points exceeds the criterion's maximum of {limit}"
            )
        if not self.reason.strip():
            raise ValueError(f"{self.criterion}: a deduction with no reason is unactionable")
        if not self.evidence.strip():
            # ponytail: non-empty, not verified against the candidate's text. Ceiling: a
            # model can quote something the draft does not say, exactly as
            # `research.record_claim` guards against for citations. The upgrade is not the
            # same check, though — a deduction for something *absent* ("there is no ask at
            # the end") has no span to quote, so verification needs a way to express that
            # before it can be switched on without failing honest deductions.
            raise ValueError(
                f"{self.criterion}: a deduction with no evidence cannot be checked or "
                "acted on — quote what is wrong"
            )


@dataclass(frozen=True)
class CriterionResult:
    """One criterion's line on the report: its ceiling, and what came off it."""

    criterion: str
    title: str
    max_points: int
    deductions: tuple[Deduction, ...] = ()

    @property
    def points_lost(self) -> int:
        return sum(deduction.points for deduction in self.deductions)

    @property
    def points_kept(self) -> int:
        """What this criterion contributes. Never negative — see `_deductions`.

        Deductions summing past the maximum are rejected as invalid model output rather
        than clamped here, because clamping invents a number: a criterion the model wanted
        to take 25 points off is not the same answer as one it took 15 off, and silently
        turning the first into the second loses the fact that the model was asked
        something it could not answer within the rubric.
        """
        return self.max_points - self.points_lost


@dataclass(frozen=True)
class ReadinessReport:
    """What one candidate was found to comply with, by which rubric, scored by which prompt.

    Not orderable and not numeric, on purpose. `dataclass(order=True)` here, or an
    `__int__`, would make `sorted(reports)` and `max(reports, key=float)` compile — and
    "which of these drafts scored highest" is one rename away from "which template scores
    highest", which is the ranking `CLAUDE.md` forbids. Both absences are pinned by tests
    so their arrival fails a build rather than passing review.

    `rubric_version` and the prompt coordinates are required fields with no defaults. A
    report that cannot say which weights produced it is a number with no denominator, and
    persisting one — a later slice — would make it permanent.
    """

    rubric_version: str
    prompt_name: str
    prompt_version: str
    criteria: tuple[CriterionResult, ...]

    # Hard-gate findings from `gates.check`, verbatim. Not re-implemented and not
    # summarised into a number: `Finding` carries no score by design (see its docstring),
    # and giving one here would make findings sortable — the back door that same docstring
    # names.
    blocking: tuple[Finding, ...] = ()

    # There is deliberately no field naming the candidate this report is about. The caller
    # has the candidate in hand — it just passed it in — and the durable answer to "which
    # template was this" is the trace row `evaluate` writes, which records the family and
    # version. A second copy here would be a third place for that pair to be wrong.

    def __post_init__(self) -> None:
        if not _SEMVER.match(self.rubric_version):
            raise ValueError(
                f"a report must record the rubric version that produced it; "
                f"{self.rubric_version!r} is not MAJOR.MINOR.PATCH"
            )
        if not self.prompt_name.strip() or not self.prompt_version.strip():
            raise ValueError("a report must record the prompt that scored the candidate")
        scored = [line.criterion for line in self.criteria]
        if scored != [criterion.key for criterion in CRITERIA]:
            # A report missing a criterion still totals *something*, and that something is
            # measured against a `A_THRESHOLD_POINTS` derived from all seven. A candidate
            # never judged on evidence support would fail the threshold it never faced.
            raise ValueError(
                f"a report covers every criterion exactly once, in order; got {scored}"
            )

    @property
    def readiness_points(self) -> int:
        """Rubric compliance out of `TOTAL_POINTS`. **Not a forecast of anything.**

        Named `readiness_points` and not `score` so that the shortest possible reading of a
        call site — `report.readiness_points` — still says what the number is about.
        """
        return sum(line.points_kept for line in self.criteria)

    @property
    def deductions(self) -> tuple[Deduction, ...]:
        """Every deduction, in rubric order. Each one carries its own evidence."""
        return tuple(deduction for line in self.criteria for deduction in line.deductions)

    @property
    def readiness(self) -> Readiness:
        """Ready for a human editor, or not — the blueprint's "A", by its real name.

        **A hard-gate failure disqualifies at any point total.** Computed, never stored, so
        there is no field a caller could set to `READY_FOR_EDITORIAL_REVIEW` on a candidate
        that failed a gate. That ordering is blueprint §11's "a language model's self-score
        alone is not a release gate", made structural: 100 points and one schema finding is
        `NEEDS_REVISION`.
        """
        if self.blocking:
            return Readiness.NEEDS_REVISION
        if self.readiness_points < A_THRESHOLD_POINTS:
            return Readiness.NEEDS_REVISION
        return Readiness.READY_FOR_EDITORIAL_REVIEW

    @property
    def summary(self) -> str:
        """One line for a person, with `DISCLAIMER` attached to the number every time."""
        verdict = (
            "ready for editorial review"
            if self.readiness is Readiness.READY_FOR_EDITORIAL_REVIEW
            else "needs revision before editorial review"
        )
        gated = f", {len(self.blocking)} hard-gate finding(s)" if self.blocking else ""
        return (
            f"{self.readiness_points}/{TOTAL_POINTS} rubric points "
            f"(rubric {self.rubric_version}){gated} — {verdict}. {DISCLAIMER}"
        )


def evaluate(
    candidate: Mapping[str, Any],
    *,
    template: Template,
    recent_posts: Sequence[str],
    idea: str,
    llm: LLM,
    session: Session,
    correlation_id: str,
    asset_values: Mapping[str, str] | None = None,
) -> ReadinessReport:
    """Score one candidate against the rubric, over the top of every hard gate.

    Takes **one** candidate. A candidate set would return a list of reports, and a list of
    reports is sorted by the first caller who wants "the best one" — see the module
    docstring. A caller with three candidates calls this three times and shows a person all
    three; choosing between them is editorial work, not arithmetic.

    Every argument is required and keyword-only past the candidate, for the reason
    `gates.check` gives about `recent_posts`: a default lets a caller quietly stop passing
    something with nothing failing, and a rubric silently judging originality against an
    empty corpus scores every draft as original.

    `correlation_id` is minted by the caller with `tracing.new_correlation_id` and shared
    with the generation that produced the candidate, so the write and its evaluation are
    one unit in the trace table. Both calls this function may make — the first attempt and
    the repair — record under it.

    Raises `RubricOutputError` if the evaluator returns output the rubric cannot use twice
    in a row. Raises `ValueError` via `gates.check` if `template` is not the VISUAL one.
    """
    # Both halves run, always, and the gates run first so that a candidate which fails one
    # still gets its rubric findings in the same pass. Blueprint §8 revises against *named
    # findings*; a caller that had to fix a schema error, re-evaluate, and only then learn
    # the hook is vague pays for two model calls and two revision rounds.
    #
    # ponytail: the model call is made even when a hard gate has already failed, which
    # spends tokens on a candidate that cannot be released as-is. Ceiling is spend, not
    # correctness. The upgrade is a `skip_evaluator_on_gate_failure` flag the autonomous
    # run sets and an interactive review does not — worth doing when a budget says so, not
    # before.
    blocking = tuple(
        gates.check(
            candidate,
            template=template,
            recent_posts=recent_posts,
            asset_values=asset_values,
        )
    )

    message = _user_message(candidate, template=template, idea=idea, recent_posts=recent_posts)
    artifacts: dict[str, Any] = {
        # Lineage through `(family_id, version)`, never the row id and never "latest" —
        # the id names a row, and a report attributed to the wrong template version is the
        # exact defect `CLAUDE.md` says this repo has already shipped three times.
        "template_family_id": template.family_id,
        "template_version": template.version,
        "rubric_version": RUBRIC_VERSION,
    }

    answer = traced_call(
        session,
        llm,
        _READINESS,
        message,
        correlation_id=correlation_id,
        input_artifact_ids=artifacts,
    )
    try:
        deductions = _deductions(answer)
    except RubricOutputError as first:
        # **One repair, written as a second statement rather than a loop.** Blueprint §11
        # permits exactly one, and the way a "max 2 attempts" loop becomes a "max 5"
        # loop is somebody editing a constant. There is no constant. A third attempt
        # requires adding a call, which is visible in review.
        #
        # `first` goes into the message *outside* the fence, and what makes that safe is
        # the `!r` on every model-supplied value in the messages it is built from — see
        # `_whole` and `Deduction.__post_init__`. `repr` escapes the newlines a model
        # would need to draw a second instruction block in here; dropping it for
        # readability puts the model's own text into the un-fenced part of the prompt.
        answer = traced_call(
            session,
            llm,
            _READINESS,
            f"{message}\n\n{_REPAIR_PREFIX}\n{first}",
            correlation_id=correlation_id,
            input_artifact_ids={**artifacts, "repair": True},
        )
        try:
            deductions = _deductions(answer)
        except RubricOutputError as second:
            # Failing with the second failure's own words, not "invalid output twice".
            # Whoever reads this has one thing to act on and it is which field was wrong.
            raise RubricOutputError(
                f"the evaluator returned output this rubric cannot use, before and after "
                f"its one repair attempt. First: {first}. After repair: {second}"
            ) from second

    return ReadinessReport(
        rubric_version=RUBRIC_VERSION,
        # Read off the prompt object rather than restated, so the report names the words
        # that were actually sent even if `_READINESS` is repointed.
        prompt_name=_READINESS.name,
        prompt_version=_READINESS.version,
        criteria=_scored(deductions),
        blocking=blocking,
    )


def _scored(deductions: Sequence[Deduction]) -> tuple[CriterionResult, ...]:
    """The deductions grouped onto every criterion, including the untouched ones.

    Every criterion appears, whether or not anything came off it. A report listing only the
    criteria with deductions cannot be told apart from one where the evaluator never
    considered the others, which is `CLAUDE.md`'s "print `—`, never `0`" rule in the
    direction it actually bites here.
    """
    return tuple(
        CriterionResult(
            criterion=criterion.key,
            title=criterion.title,
            max_points=criterion.max_points,
            deductions=tuple(d for d in deductions if d.criterion == criterion.key),
        )
        for criterion in CRITERIA
    )


# Prefixed to the original message on the one repair attempt. The original message is
# repeated in full rather than sending the complaint alone: the model has no memory of the
# first call, and a bare "your points field was not a number" is not a task.
_REPAIR_PREFIX = (
    "Your previous answer could not be used. Return the same JSON shape, fixing exactly "
    "this and changing nothing else about your judgement:"
)


def _deductions(answer: Mapping[str, Any]) -> list[Deduction]:
    """Validate the evaluator's answer into deductions, or say precisely what is wrong.

    Every rejection message names the deduction's position and the field, because it is
    what gets sent back on the repair attempt and then, if that fails too, what a person
    reads.
    """
    proposed = answer.get("deductions")
    if proposed is None:
        raise RubricOutputError(
            "missing key 'deductions'; a draft with nothing wrong returns an empty list"
        )
    if not isinstance(proposed, list):
        raise RubricOutputError(f"'deductions' must be an array, got {type(proposed).__name__}")

    deductions: list[Deduction] = []
    for position, item in enumerate(proposed):
        where = f"deductions[{position}]"
        if not isinstance(item, Mapping):
            raise RubricOutputError(f"{where} must be an object, got {type(item).__name__}")
        missing = [key for key in ("criterion", "points", "reason", "evidence") if key not in item]
        if missing:
            raise RubricOutputError(f"{where} is missing {missing}")
        try:
            deductions.append(
                Deduction(
                    criterion=str(item["criterion"]),
                    points=_whole(item["points"], where),
                    reason=str(item["reason"]),
                    evidence=str(item["evidence"]),
                )
            )
        except ValueError as exc:
            # `Deduction.__post_init__` already phrased this in terms a model can act on;
            # the position is what it could not know.
            raise RubricOutputError(f"{where}: {exc}") from exc

    _within_maxima(deductions)
    return deductions


def _whole(value: Any, where: str) -> int:
    """A whole number of points, accepting the `5.0` a JSON encoder may have written.

    `5.0` is the same answer as `5` and refusing it would spend the one repair attempt on
    a formatting difference. `5.5` is not the same answer as anything and is refused: a
    fractional deduction has no meaning against a rubric of whole points, and rounding it
    would be this module choosing the score.
    """
    if isinstance(value, bool):
        raise RubricOutputError(f"{where}: points must be a whole number, got a boolean")
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    # `!r`, not `!s`. This message is quoted back to the model on the repair attempt and
    # `value` is whatever the model wrote; `repr` escapes the newlines it would need to
    # address the model from inside its own error message.
    raise RubricOutputError(f"{where}: points must be a whole number, got {value!r}")


def _within_maxima(deductions: Sequence[Deduction]) -> None:
    """No criterion loses more than it is worth, counting every deduction against it.

    Each deduction is already capped individually by `Deduction.__post_init__`; three of
    them against one 10-point criterion are not. Rejected rather than clamped — see
    `CriterionResult.points_kept` for why clamping would be this module inventing a number.
    """
    for criterion in CRITERIA:
        taken = sum(d.points for d in deductions if d.criterion == criterion.key)
        if taken > criterion.max_points:
            raise RubricOutputError(
                f"{criterion.key}: deductions total {taken} points against a maximum of "
                f"{criterion.max_points}; take fewer or take less"
            )


# --- what the evaluator is shown ------------------------------------------------------------

# The fence, same construction as `research._OPEN` and for the same reason: a marker that
# could plausibly occur in prose is not a marker. Written out here rather than imported
# because `research`'s blocks are shaped for a fetched web page — label, url, title — and
# because importing `app.research` to borrow four characters would put the rubric behind
# `fetching`, `autonomous` and the search provider seam at import time.
_OPEN = "<<<PIXII-{label}>>>"
_CLOSE = "<<<END PIXII-{label}>>>"

# Rewritten wherever they occur inside a block. `<<<` and `>>>` are the only sequences a
# marker is built from, so text inside a block can neither close it nor open another.
_FORGERY = (("<<<", "‹‹‹"), (">>>", "›››"))

# How much of each recent post the originality criterion is shown. Whole posts would push
# the draft under review far down a long message for a criterion worth 10 points.
_RECENT_EXCERPT_CHARS = 400

# ponytail: the most recent posts the caller passed, in the order given, capped here.
# Ceiling: the cap is arbitrary and the caller chooses the window, exactly as `gates`
# leaves "recent" to the query. Raise it when a real corpus says the model is missing
# repeats, which needs posts with recorded lineage — there are zero.
_RECENT_LIMIT = 8


def _fenced(label: str, text: str) -> str:
    """One block of quoted material, unable to address the model from inside itself."""
    for forged, replacement in _FORGERY:
        text = text.replace(forged, replacement)
    return f"{_OPEN.format(label=label)}\n{text}\n{_CLOSE.format(label=label)}"


def _user_message(
    candidate: Mapping[str, Any],
    *,
    template: Template,
    idea: str,
    recent_posts: Sequence[str],
) -> str:
    """The draft, its slot text, the idea it came from, and what was posted recently.

    The candidate is fenced even though this application generated it. It is not
    first-party the way an operator's brief is: a draft written against a research dossier
    carries whatever a fetched page put into that dossier, and blueprint invariant 7 does
    not stop applying one hop downstream.
    """
    hook = str(candidate.get("hook") or "").strip()
    body = str(candidate.get("body") or "").strip()
    # Identical to `gates.check`'s `full_text` and to `Draft.full_text`. The evaluator has
    # to judge the string that would publish; scoring a differently-joined one would make
    # a structure deduction land on a paragraph break this module invented.
    full_text = f"{hook}\n\n{body}".strip()

    supplied = candidate.get("visual_values")
    written: Mapping[str, Any] = supplied if isinstance(supplied, Mapping) else {}

    parts = [
        f"Idea the draft was written from: {idea.strip() or '(none supplied)'}",
        f"Visual template: {template.name}",
        _fenced("DRAFT", full_text),
    ]
    if written:
        # Slot text is rendered into the image and published alongside the post, so it is
        # part of the draft under review — the same reason `gates._forbidden_claims` scans
        # it. Shown separately because it is not prose and should not be read as flow.
        slots = "\n".join(f"{name}: {value}" for name, value in written.items())
        parts.append(_fenced("VISUAL-SLOT-TEXT", slots))
    recent = [post.strip() for post in recent_posts[:_RECENT_LIMIT] if post.strip()]
    if recent:
        excerpts = "\n\n".join(post[:_RECENT_EXCERPT_CHARS] for post in recent)
        parts.append(
            "Recent posts, for the originality criterion only:\n" + _fenced("RECENT", excerpts)
        )
    else:
        # Said out loud rather than left out. A silently absent corpus reads to the model
        # as "nothing similar exists", and it would deduct — or not — on an impression.
        #
        # ponytail: the candidate then keeps all 10 originality points, which credits an
        # absence of data, and that is `CLAUDE.md`'s `—`-versus-`0` rule going the wrong
        # way. It is stated rather than hidden. Ceiling: `CriterionResult` cannot say "not
        # assessed", so there is nowhere to put the distinction; the upgrade is that flag
        # plus a `readiness` that refuses `READY_FOR_EDITORIAL_REVIEW` while any criterion
        # is unassessed, and it wants a caller that can actually supply a corpus first.
        parts.append(
            "No recent posts were supplied, so originality against the recent corpus "
            "cannot be assessed. Take no deduction under 'originality'."
        )
    return "\n\n".join(parts)


__all__ = [
    "A_THRESHOLD_POINTS",
    "CRITERIA",
    "DISCLAIMER",
    "RUBRIC_VERSION",
    "TOTAL_POINTS",
    "Criterion",
    "CriterionResult",
    "Deduction",
    "Readiness",
    "ReadinessReport",
    "RubricOutputError",
    "evaluate",
]
