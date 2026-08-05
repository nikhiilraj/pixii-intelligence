"""The workflow state contract, and the two things it exists to stop.

`GenerationStage` replaced three hand-written copies of "which stages may a human act on" —
one at the push boundary, a different one at retry, and none at all on the publication
routes. These tests pin the contract itself; the routes' use of it is tested where the routes
are.
"""

import pytest

from app.models.stage import (
    ORDER,
    GenerationStage,
    advance,
    in_flight,
    retryable,
    review_ready,
    stage_of,
    terminal,
)

# Written out as literals, independently of the enum, on purpose. Comparing the enum to
# itself proves nothing; this list is one half of the backend/frontend contract and its twin
# is `STAGES` in frontend/src/lib/api.ts. Adding a stage to one side and not the other fails
# here or there, which is the drift the pair exists to catch.
DECLARED = [
    "unreviewed",
    "planning",
    "researching",
    "drafting",
    "verifying",
    "revising",
    "evaluating",
    "rendering",
    "ready",
    "failed",
    "failed_review",
]


def test_the_declared_stages_are_exactly_the_enums():
    assert [stage.value for stage in GenerationStage] == DECLARED


def test_only_ready_is_review_ready():
    """The predicate the push, schedule and publish boundaries all compose."""
    for stage in GenerationStage:
        assert review_ready(stage) is (stage is GenerationStage.READY), stage


def test_a_draft_nothing_reviewed_is_not_review_ready():
    """`unreviewed` is the default, and the default must not be actionable.

    The old default was `ready`, so every path that did no review at all — variants,
    retopic, autonomous, legacy `POST /drafts` — minted a draft a human could push.
    """
    assert review_ready(GenerationStage.UNREVIEWED) is False


def test_an_unknown_stage_from_an_older_row_is_not_review_ready():
    """The column is a plain `str`. A value this version cannot name is not vouched for."""
    assert stage_of("something_a_later_version_wrote") is None
    assert review_ready("something_a_later_version_wrote") is False
    assert terminal("something_a_later_version_wrote") is True


def test_only_the_two_failures_are_retryable():
    for stage in GenerationStage:
        expected = stage in {GenerationStage.FAILED, GenerationStage.FAILED_REVIEW}
        assert retryable(stage) is expected, stage


def test_unreviewed_is_terminal_because_nothing_will_run():
    assert in_flight(GenerationStage.UNREVIEWED) is False
    assert terminal(GenerationStage.UNREVIEWED) is True


def test_in_flight_and_terminal_partition_every_stage():
    for stage in GenerationStage:
        assert in_flight(stage) is not terminal(stage), stage


def test_a_workflow_starts_at_planning():
    assert advance(None, GenerationStage.PLANNING) is GenerationStage.PLANNING
    assert advance(GenerationStage.UNREVIEWED, GenerationStage.PLANNING) is GenerationStage.PLANNING


def test_a_workflow_cannot_start_part_way_through():
    """Beginning at `drafting` is a run that skipped the brief — the bypass, in one line."""
    with pytest.raises(ValueError, match="starts at"):
        advance(None, GenerationStage.DRAFTING)


def test_progress_may_skip_forward_but_never_go_back():
    # `none` mode never enters researching; a clean candidate never enters revising.
    assert advance(GenerationStage.PLANNING, GenerationStage.DRAFTING) is GenerationStage.DRAFTING
    with pytest.raises(ValueError, match="cannot move back"):
        advance(GenerationStage.EVALUATING, GenerationStage.DRAFTING)


def test_a_stage_cannot_advance_to_itself():
    """Otherwise a stalled run reports progress by rewriting the stage it is already on."""
    for stage in ORDER:
        with pytest.raises(ValueError, match="cannot move back"):
            advance(stage, stage)


def test_any_stage_may_reach_a_terminal():
    """A failure arrives whenever it arrives; nothing may refuse to record one."""
    for stage in GenerationStage:
        for end in (GenerationStage.READY, GenerationStage.FAILED, GenerationStage.FAILED_REVIEW):
            assert advance(stage, end) is end


def test_a_terminal_stage_does_not_advance():
    """Retry writes a new draft. Reusing the failed row would destroy the audit record."""
    with pytest.raises(ValueError, match="terminal"):
        advance(GenerationStage.FAILED_REVIEW, GenerationStage.PLANNING)
