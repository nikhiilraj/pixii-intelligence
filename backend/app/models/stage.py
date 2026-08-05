"""Where a draft is in the review workflow, and what that permits.

One enum and four predicates, because the alternative was in the tree until this file
existed: `if draft.generation_stage in {"failed", "failed_review"}` written out at the push
boundary, a different set written out at the retry boundary, and the publication routes
checking neither. Three hand-maintained copies of one rule, and the one that was missing was
the one guarding the most consequential action in the application.

**There is deliberately no separate `publishable()`.** Publishing additionally requires a
pushed post and a matching revision, but at the *stage* level the question is identical to
`review_ready` — and two names for one predicate is how the copies got out of step in the
first place. `distribution.submit` composes `review_ready` with its own two checks, where
they can be read together.

Lives under `models/` beside `models/research.py`, which holds its mode and claim-status
constants the same way, rather than in a module that also does work.
"""

from enum import StrEnum


class GenerationStage(StrEnum):
    """Every state a draft's generation can be in. The column stores the value.

    A `StrEnum` rather than bare constants so `Draft.generation_stage` — still a plain `str`
    column, so no migration and no coercion of historical rows — compares equal to a member
    without a cast, and so an unknown value from an older row is a value this enum can be
    asked about without raising.
    """

    # Written by a path that does no review at all: legacy `POST /drafts`, and anything that
    # constructs a `Draft` without saying where it came from. **This is the default**, and it
    # is not review-ready, which is the whole point: a draft nothing has checked must not be
    # pushable merely because nobody set a column. The old default was `ready`.
    UNREVIEWED = "unreviewed"

    # In flight. Each is a real stage of `generation.generate_reviewed_draft`, committed as it
    # is entered so a reader sees where a run actually is rather than where it ended.
    PLANNING = "planning"
    RESEARCHING = "researching"
    DRAFTING = "drafting"
    VERIFYING = "verifying"
    REVISING = "revising"
    EVALUATING = "evaluating"
    RENDERING = "rendering"

    # Terminal.
    READY = "ready"
    # The workflow could not produce a candidate — planning, research or the write failed.
    FAILED = "failed"
    # A candidate exists and did not pass. Distinct from `FAILED` because the words are there
    # to read: a reviewer can see what was written and why it was refused.
    FAILED_REVIEW = "failed_review"


IN_FLIGHT = frozenset(
    {
        GenerationStage.PLANNING,
        GenerationStage.RESEARCHING,
        GenerationStage.DRAFTING,
        GenerationStage.VERIFYING,
        GenerationStage.REVISING,
        GenerationStage.EVALUATING,
        GenerationStage.RENDERING,
    }
)

TERMINAL = frozenset(
    {GenerationStage.READY, GenerationStage.FAILED, GenerationStage.FAILED_REVIEW}
)

# The order the reviewed workflow moves through. Used by `advance` and by nothing else — a
# stage may always jump to a terminal, so this describes progress, not permission.
ORDER: tuple[GenerationStage, ...] = (
    GenerationStage.PLANNING,
    GenerationStage.RESEARCHING,
    GenerationStage.DRAFTING,
    GenerationStage.VERIFYING,
    GenerationStage.REVISING,
    GenerationStage.EVALUATING,
    GenerationStage.RENDERING,
)


def stage_of(value: str | None) -> GenerationStage | None:
    """The enum member for a stored value, or None for one this version does not know.

    `None` rather than a raise: the column is a plain `str` and rows written by an older
    version of this application are readable by construction. A caller that cannot name the
    stage treats it as not review-ready, which is the safe direction.
    """
    try:
        return GenerationStage(value or "")
    except ValueError:
        return None


def review_ready(value: str | None) -> bool:
    """Whether a human's review may act on this draft — push it, schedule it, publish it.

    Only `ready`. An in-flight draft is not ready because it is not finished; an unknown
    value is not ready because nothing here can vouch for it.
    """
    return stage_of(value) is GenerationStage.READY


def retryable(value: str | None) -> bool:
    """Whether `POST /drafts/{id}/retry` may start a new attempt from this one.

    Both failure states, and neither success nor in-flight: retrying a run that is still
    going would buy a second set of billed calls for the same idea.
    """
    return stage_of(value) in {GenerationStage.FAILED, GenerationStage.FAILED_REVIEW}


def in_flight(value: str | None) -> bool:
    """Whether a workflow is still working on this draft."""
    return stage_of(value) in IN_FLIGHT


def terminal(value: str | None) -> bool:
    """Whether this draft has stopped moving. `unreviewed` is terminal — nothing will run.

    An unknown value counts as terminal for the same reason it is not review-ready: whatever
    wrote it is not this version, so nothing here is going to advance it.
    """
    stage = stage_of(value)
    return stage is None or stage not in IN_FLIGHT


def advance(current: str | None, to: GenerationStage) -> GenerationStage:
    """The next stage, refusing a transition the workflow cannot actually make.

    Two rules, and they are the whole map:

    - **Any stage may reach a terminal.** A failure can arrive at any point, and a map that
      enumerated every stage's route to `failed` would be a table nobody could read for the
      one property it exists to state.
    - **Progress only moves forward through `ORDER`.** A run that went back to `drafting`
      after `evaluating` would show a reviewer a stage the words have already left, and the
      Studio poll would read it as progress. Skipping forward is allowed — `none` mode never
      enters `researching`, and a candidate with no findings never enters `revising`.

    Raises `ValueError` rather than clamping. A clamp would let the bug through and report
    the stage it wished were true.
    """
    if to in TERMINAL:
        return to
    stage = stage_of(current)
    if stage is None or stage is GenerationStage.UNREVIEWED:
        # Starting a run. Only from the top: a workflow that began at `drafting` skipped the
        # brief, which is the bypass this whole slice exists to close.
        if to is not GenerationStage.PLANNING:
            raise ValueError(f"a workflow starts at {GenerationStage.PLANNING}, not {to}")
        return to
    if stage in TERMINAL:
        raise ValueError(f"{stage} is terminal; a retry is a new draft, not a new stage here")
    if ORDER.index(to) <= ORDER.index(stage):
        raise ValueError(f"{stage} cannot move back to {to}")
    return to


__all__ = [
    "IN_FLIGHT",
    "ORDER",
    "TERMINAL",
    "GenerationStage",
    "advance",
    "in_flight",
    "retryable",
    "review_ready",
    "stage_of",
    "terminal",
]
