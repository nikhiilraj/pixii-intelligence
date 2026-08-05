"""What a brief and a claim plan are allowed to be, and what they may never be.

Four things are load-bearing here and each has its own section: a claim plan is a list of
individually addressable claims and never a paragraph; research depth is `research.resolve_mode`'s
answer and never this module's; every generated artifact says which prompt version wrote it;
and NULL means "nobody said" rather than zero.

No model is called anywhere in this file. `FakeLLM` answers from a list and records what it
was sent, which is the only text that matters when the question is what reached the model.
"""

import re
from datetime import UTC, datetime

import pytest
from sqlmodel import col, select

from app import research
from app.db import utc
from app.editorial import (
    CLAIM_MAX_CHARS,
    UnplannableClaims,
    build_brief,
    plan_angle,
    planned_claims,
    research_question,
)
from app.models.editorial import CHANNEL, AnglePlan, EditorialBrief, PlannedClaim
from app.models.generation_trace import GenerationTrace
from app.models.research import DEEP, LIGHT, NONE
from app.prompts.editorial import ANGLE_PLAN, BRIEF, PROMPTS
from app.prompts.registry import index
from app.research import ModeBelowFloor

# A brief that leans on the outside world: a year, a named body, a number.
FACTUAL = "What the EU AI Act changed for model providers in 2026"

# A brief that leans on nobody. Lower-case throughout on purpose — the floor detector treats a
# capitalised word mid-sentence as a named thing, which is the bias it is supposed to have.
OPINION = "why i think meetings without an agenda waste everyone time"

BRIEF_ANSWER = {
    "objective": "be the account people quote on this",
    "audience": "heads of engineering at 50-200 person product companies",
    "desired_action": "reply with how their own team handles it",
    "constraints": ["no jargon", "under 200 words"],
}

PLAN_ANSWER = {
    "thesis": "an agenda is the cheapest scheduling tool a team owns",
    "tension": "everyone agrees and nobody writes one",
    "audience_stake": "their calendars are the thing being spent",
    "claims": [
        {"text": "a meeting without a stated question has no way to end early"},
        {"text": "writing the agenda is what surfaces the disagreement"},
    ],
    "beats": ["open on the cost", "name the objection", "land the ask"],
    "cta": "reply with your own rule",
}

# One entry, holding a paragraph. Structurally perfect — a list, of a dict, with a non-empty
# `text` — and exactly the prose blob the claim plan exists to refuse.
BLOB = (
    "Meetings are expensive and everybody knows it. The agenda is the thing that makes them "
    "cheaper, because it forces somebody to say what the meeting is for before it starts. "
    "Teams that write one leave earlier, teams that do not sit through the whole hour, and "
    "the difference compounds across a quarter in ways nobody bothers to measure."
)


class FakeLLM:
    """Answers with `responses` in turn and records exactly what it was sent."""

    def __init__(self, *responses: dict):
        self.responses = list(responses)
        self.calls: list[tuple[str, str, tuple]] = []

    def complete_json(self, system: str, user: str, images=()) -> dict:
        self.calls.append((system, user, tuple(images)))
        return self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]


def _brief(session, llm=None, *, idea=OPINION, **kwargs) -> EditorialBrief:
    return build_brief(session, llm or FakeLLM(BRIEF_ANSWER), idea=idea, **kwargs)


# --- the prompts this slice registers -----------------------------------------------------------
#
# `test_prompts.py` runs these invariants over `prompts.ALL`, which does not include this slice
# until the library aggregates it. Until then these are the only place they are checked, and
# after aggregation they are a second, local check — which is cheap and outlives the wave.


def test_both_prompts_index_without_collision_and_are_namespaced():
    table = index(PROMPTS)
    assert len(table) == len(PROMPTS)
    assert all(prompt.name.startswith("editorial.") for prompt in PROMPTS)


def test_every_prompt_states_the_json_shape_inside_its_own_text():
    for prompt in PROMPTS:
        assert "Return ONLY JSON" in prompt.text


def test_every_prompt_carries_a_major_minor_patch_version():
    for prompt in PROMPTS:
        assert re.fullmatch(r"\d+\.\d+\.\d+", prompt.version)


def test_every_prompt_declares_an_object_schema_whose_required_keys_it_defines():
    for prompt in PROMPTS:
        schema = prompt.output_schema
        assert schema["type"] == "object"
        assert set(schema.get("required", [])) <= set(schema["properties"])


def test_the_plan_prompt_asks_for_claims_as_objects_and_not_as_prose():
    """A bare string list would make a claim a sentence in an array rather than a thing.

    The artifact's whole job is that a claim has an identity a citation can point at, so it is
    asked for as an object with a `text` key from the moment it is requested.
    """
    claims = ANGLE_PLAN.output_schema["properties"]["claims"]
    assert claims["type"] == "array"
    assert claims["items"]["type"] == "object"
    assert claims["items"]["required"] == ["text"]


def test_no_prompt_asks_the_model_to_rank_anything():
    """`CLAUDE.md`: never rank. This fails the moment a prompt learns to compare angles."""
    for prompt in PROMPTS:
        for banned in ("best", "top", "recommend", "rank", "score", "outperform", "winner"):
            assert not re.search(rf"\b{banned}", prompt.text, re.I), (prompt.name, banned)


def test_the_brief_prompt_forbids_the_model_deciding_research_depth():
    """The one policy this slice may not hold a second copy of, stated to the model too."""
    assert "Do not decide how much research this needs" in BRIEF.text
    assert "research_depth" not in BRIEF.output_schema["properties"]


# --- the brief ---------------------------------------------------------------------------------


def test_a_brief_records_what_the_idea_is_for_and_who_it_is_for(session):
    brief = _brief(session)

    assert brief.idea == OPINION
    assert brief.objective == BRIEF_ANSWER["objective"]
    assert brief.audience == BRIEF_ANSWER["audience"]
    assert brief.desired_action == BRIEF_ANSWER["desired_action"]
    assert brief.constraints == ["no jargon", "under 200 words"]
    assert brief.channel == CHANNEL


def test_a_brief_records_the_prompt_version_that_wrote_it(session):
    """Lineage: the artifact says which words produced it, not merely that a model did."""
    brief = _brief(session)

    assert (brief.prompt_name, brief.prompt_version) == (BRIEF.name, BRIEF.version)


def test_the_brief_call_is_traced_under_the_brief_own_correlation_id(session):
    brief = _brief(session)

    trace = session.exec(
        select(GenerationTrace).where(GenerationTrace.correlation_id == brief.correlation_id)
    ).one()
    assert (trace.prompt_name, trace.prompt_version) == (BRIEF.name, BRIEF.version)


def test_an_unstated_research_preference_is_null_and_not_the_none_mode(session):
    """NULL is "the operator said nothing"; `'none'` is a request they never made.

    Not a tidiness point. `resolve_mode` refuses `none` for any brief that leans on the outside
    world, so a column defaulted to `'none'` would turn every factual brief into a failure at
    the door — and a column defaulted to `''` would be an unknown mode.
    """
    brief = _brief(session)

    assert brief.requested_mode is None


def test_a_brief_nobody_scheduled_records_no_time_rather_than_now(session):
    """`now()` here would be the moment the brief was written wearing a publication plan."""
    brief = _brief(session)

    assert brief.proposed_time is None


def test_a_proposed_time_survives_the_round_trip_through_the_naive_column(session):
    """Every datetime column is `timestamp without time zone`; `db.utc` is what reconciles it.

    The expire is the whole test. Without it this asserts on the aware object just assigned and
    would pass against a column that cannot hold a timezone at all — which is every column here.
    """
    when = datetime.now(UTC)
    brief = _brief(session, proposed_time=when)

    session.flush()
    session.expire(brief)

    assert brief.proposed_time is not None
    assert brief.proposed_time.tzinfo is None  # read back naive, as the column stores it
    assert utc(brief.proposed_time) == when


def test_the_research_depth_is_whatever_resolve_mode_answered(session, monkeypatch):
    """The mutation this guards: a rule of this module's own deciding depth.

    `resolve_mode` is replaced with one that answers `deep` for a brief no floor signal would
    fire on. A module holding a second policy would disagree with it and this fails.
    """
    monkeypatch.setattr(
        research,
        "resolve_mode",
        lambda question, requested=None: research.Resolved(
            mode=DEEP, recommended=DEEP, signals=("fabricated",)
        ),
    )
    brief = _brief(session)

    assert (brief.research_mode, brief.recommended_mode) == (DEEP, DEEP)
    assert brief.mode_signals == ["fabricated"]


def test_a_brief_that_leans_on_the_outside_world_gets_the_light_floor(session):
    brief = _brief(session, idea=FACTUAL)

    assert brief.recommended_mode == LIGHT
    assert brief.research_mode == LIGHT
    assert brief.mode_signals  # and it says what it saw


def test_a_brief_that_leans_on_nobody_needs_no_research(session):
    brief = _brief(session)

    assert (brief.recommended_mode, brief.research_mode) == (NONE, NONE)
    assert brief.mode_signals == []


def test_asking_for_no_research_on_a_factual_brief_is_refused_and_writes_nothing(session):
    """The floor is not negotiable, and a refused brief leaves no row claiming it was fine."""
    with pytest.raises(ModeBelowFloor):
        _brief(session, idea=FACTUAL, requested_mode=NONE)

    assert session.exec(select(EditorialBrief)).all() == []


def test_an_operator_may_raise_the_depth_and_the_request_is_kept_beside_the_result(session):
    brief = _brief(session, idea=FACTUAL, requested_mode=DEEP)

    assert brief.requested_mode == DEEP
    assert brief.research_mode == DEEP
    # The floor is still recorded as what it was. One column could not answer "was the
    # recommended depth honoured, or exceeded".
    assert brief.recommended_mode == LIGHT


# --- the claim plan ----------------------------------------------------------------------------


def test_each_planned_claim_is_its_own_row_with_its_own_id(session):
    """What makes research checkable later: a citation has something to point at."""
    llm = FakeLLM(BRIEF_ANSWER, PLAN_ANSWER)
    brief = _brief(session, llm)
    plan = plan_angle(session, llm, brief)

    claims = planned_claims(session, plan)
    assert [claim.text for claim in claims] == [c["text"] for c in PLAN_ANSWER["claims"]]
    assert all(claim.id is not None for claim in claims)
    assert len({claim.id for claim in claims}) == 2


def test_a_plan_records_the_thesis_the_tension_the_stake_the_beats_and_the_cta(session):
    llm = FakeLLM(BRIEF_ANSWER, PLAN_ANSWER)
    plan = plan_angle(session, llm, _brief(session, llm))

    assert plan.thesis == PLAN_ANSWER["thesis"]
    assert plan.tension == PLAN_ANSWER["tension"]
    assert plan.audience_stake == PLAN_ANSWER["audience_stake"]
    assert plan.beats == PLAN_ANSWER["beats"]
    assert plan.cta == PLAN_ANSWER["cta"]


def test_a_plan_records_the_prompt_version_that_wrote_it_and_names_its_brief(session):
    llm = FakeLLM(BRIEF_ANSWER, PLAN_ANSWER)
    brief = _brief(session, llm)
    plan = plan_angle(session, llm, brief)

    assert (plan.prompt_name, plan.prompt_version) == (ANGLE_PLAN.name, ANGLE_PLAN.version)
    assert plan.brief_id == brief.id

    trace = session.exec(
        select(GenerationTrace)
        .where(GenerationTrace.prompt_name == ANGLE_PLAN.name)
        .order_by(col(GenerationTrace.id).desc())
    ).first()
    assert trace is not None
    assert trace.prompt_version == ANGLE_PLAN.version
    assert trace.input_artifact_ids == {"editorial_brief": brief.id}


def test_a_claim_plan_that_came_back_as_prose_is_refused(session):
    """A paragraph where the claims should be is not a claim plan, and is not stored as one."""
    llm = FakeLLM(BRIEF_ANSWER, {**PLAN_ANSWER, "claims": BLOB})
    brief = _brief(session, llm)

    with pytest.raises(UnplannableClaims):
        plan_angle(session, llm, brief)

    assert session.exec(select(AnglePlan)).all() == []
    assert session.exec(select(PlannedClaim)).all() == []


def test_a_single_claim_holding_a_paragraph_is_refused(session):
    """The prose blob that passes every structural check: a list, a dict, a non-empty string.

    This is the one an `isinstance` check cannot catch, and the reason `CLAIM_MAX_CHARS` exists.
    """
    llm = FakeLLM(BRIEF_ANSWER, {**PLAN_ANSWER, "claims": [{"text": BLOB}]})
    brief = _brief(session, llm)

    with pytest.raises(UnplannableClaims):
        plan_angle(session, llm, brief)

    assert session.exec(select(AnglePlan)).all() == []
    assert len(BLOB) > CLAIM_MAX_CHARS


def test_a_claim_that_is_three_sentences_is_not_one_claim(session):
    """Short enough to pass the length bound and still an argument rather than a statement."""
    argument = "Agendas help. Nobody writes them. That gap is the whole problem here."
    assert len(argument) < CLAIM_MAX_CHARS

    llm = FakeLLM(BRIEF_ANSWER, {**PLAN_ANSWER, "claims": [{"text": argument}]})
    with pytest.raises(UnplannableClaims):
        plan_angle(session, llm, _brief(session, llm))


def test_a_claim_that_is_one_very_long_sentence_is_not_addressable_either(session):
    """The length bound on its own, with the sentence bound unable to help.

    Written after a mutation run: raising `CLAIM_MAX_CHARS` alone failed nothing, because the
    paragraph above is also four sentences and the other bound caught it. One run-on sentence
    is the case only the length can refuse, and it is what a model produces when it tries to
    fit an argument into "one sentence".
    """
    runon = "a meeting with no stated question cannot end early and " * 6 + "that is the cost."

    llm = FakeLLM(BRIEF_ANSWER, {**PLAN_ANSWER, "claims": [{"text": runon}]})
    with pytest.raises(UnplannableClaims):
        plan_angle(session, llm, _brief(session, llm))

    # Asserted after the behaviour, not before it: a precondition that fails first turns a
    # loosened bound into a broken test rather than a caught mutation.
    assert len(runon) > CLAIM_MAX_CHARS
    assert runon.count(".") == 1  # so the sentence bound cannot be what refused it


def test_one_unaddressable_claim_costs_that_claim_and_not_the_plan(session):
    """Dropping is safe in one direction only, and this is that direction.

    A dropped claim is one the post will not be asked to make. Reading the paragraph
    charitably as a claim would do the opposite: an assertion nothing can be mapped to, with
    the draft making all of it anyway.
    """
    llm = FakeLLM(
        BRIEF_ANSWER,
        {**PLAN_ANSWER, "claims": [{"text": BLOB}, *PLAN_ANSWER["claims"]]},
    )
    plan = plan_angle(session, llm, _brief(session, llm))

    assert [c.text for c in planned_claims(session, plan)] == [
        c["text"] for c in PLAN_ANSWER["claims"]
    ]


def test_the_claims_are_read_back_in_the_order_the_plan_stated_them(session):
    """By id, which is insertion order, and by nothing that could read as a ranking."""
    texts = [f"claim number {n} says a checkable thing" for n in range(5)]
    llm = FakeLLM(BRIEF_ANSWER, {**PLAN_ANSWER, "claims": [{"text": t} for t in texts]})
    plan = plan_angle(session, llm, _brief(session, llm))

    assert [c.text for c in planned_claims(session, plan)] == texts


def test_what_the_post_must_not_repeat_is_the_caller_list_and_not_the_model_echo(session):
    """A model handing the list back can drop an entry, and a repeat post is the failure."""
    recent = ["agendas", "standups"]
    llm = FakeLLM(BRIEF_ANSWER, {**PLAN_ANSWER, "must_not_repeat": ["something else"]})
    plan = plan_angle(session, llm, _brief(session, llm), recent_topics=recent)

    assert plan.must_not_repeat == recent
    # And the model was told, so the plan it wrote is not merely audited against the list.
    assert "standups" in llm.calls[-1][1]


def test_a_plan_has_no_field_that_could_hold_a_second_angle_or_a_score(session):
    """`CLAUDE.md`: never rank. A set of angles invites an ordering; an ordering is a ranking.

    This is the assertion that fails the moment someone adds `alternatives` or `angle_score`.
    """
    fields = set(AnglePlan.model_fields)
    assert "thesis" in fields
    for banned in ("score", "rank", "alternative", "options", "confidence", "best"):
        assert not [name for name in fields if banned in name], banned


# --- the claims can raise the research depth, and can never lower it ---------------------------


def test_a_claim_naming_a_company_raises_the_floor_the_idea_alone_missed(session):
    """The reason the depth is asked again after planning.

    The idea is an opinion and trips nothing. The plan then intends to assert something about a
    named company in a named year, which is precisely the sentence that must not reach a draft
    uncited — so the brief's depth moves up to match what the post now intends to say.
    """
    brief = _brief(session)
    assert brief.research_mode == NONE

    llm = FakeLLM(
        {**PLAN_ANSWER, "claims": [{"text": "Microsoft cut its meeting length in 2025"}]}
    )
    plan_angle(session, llm, brief)

    assert brief.recommended_mode == LIGHT
    assert brief.research_mode == LIGHT
    assert "organisation" in brief.mode_signals


def test_claims_that_add_nothing_leave_the_depth_where_it_was(session):
    """Monotone, and only monotone: re-resolving may raise a depth and may never lower one."""
    llm = FakeLLM(BRIEF_ANSWER, PLAN_ANSWER)
    brief = _brief(session, llm, idea=FACTUAL)
    assert brief.research_mode == LIGHT

    plan_angle(session, llm, brief)

    assert brief.research_mode == LIGHT
    assert brief.recommended_mode == LIGHT


def test_re_planning_a_brief_cannot_lower_the_depth_an_earlier_plan_raised(session):
    """The downgrade a second plan could otherwise walk the brief back through.

    A brief may have more than one plan. The first intends to assert a company fact and moves
    the depth to `light`; the second is blander. Re-resolving from the second plan's claims
    alone would put the brief back to `none` while the first plan's claims — and anything
    written from them — still exist, which is the silent downgrade the floor exists to prevent.
    """
    brief = _brief(session)
    first = FakeLLM(
        {**PLAN_ANSWER, "claims": [{"text": "Microsoft cut its meeting length in 2025"}]}
    )
    plan_angle(session, first, brief)
    assert brief.research_mode == LIGHT

    plan_angle(session, FakeLLM(PLAN_ANSWER), brief)

    assert brief.research_mode == LIGHT
    assert brief.recommended_mode == LIGHT


def test_a_plan_whose_claims_outgrow_the_requested_depth_is_refused(session):
    """The floor bypass this stage could most easily have shipped.

    The operator asked for `none` and the brief agreed, because the idea alone leans on nobody.
    The claims then name a company and a year. Writing the plan anyway would leave a `none`-mode
    run with an externally checkable claim in it — an uncited fact, which is the failure the
    whole slice exists to prevent.
    """
    brief = _brief(session, requested_mode=NONE)
    assert brief.research_mode == NONE

    llm = FakeLLM(
        {**PLAN_ANSWER, "claims": [{"text": "Microsoft cut its meeting length in 2025"}]}
    )
    with pytest.raises(ModeBelowFloor):
        plan_angle(session, llm, brief)

    assert session.exec(select(AnglePlan)).all() == []
    assert session.exec(select(PlannedClaim)).all() == []
    # And the brief still says what it said. A refused plan may not leave the depth half-moved.
    assert brief.research_mode == NONE


def test_the_plan_stage_asks_resolve_mode_and_does_not_decide_depth_itself(session, monkeypatch):
    """The same mutation as the brief's, at the stage that re-resolves."""
    llm = FakeLLM(BRIEF_ANSWER, PLAN_ANSWER)
    brief = _brief(session, llm)

    monkeypatch.setattr(
        research,
        "resolve_mode",
        lambda question, requested=None: research.Resolved(
            mode=DEEP, recommended=DEEP, signals=("fabricated",)
        ),
    )
    plan_angle(session, llm, brief)

    assert brief.research_mode == DEEP
    assert brief.mode_signals == ["fabricated"]


# --- the question the floor is asked about ------------------------------------------------------


def test_a_claim_that_starts_with_a_company_name_is_visible_to_the_floor():
    """Why every line is bulleted, in one assertion.

    `research._names_a_thing` ignores a capitalised word at the start of a sentence. A claim
    whose first word *is* the company is therefore invisible when it stands alone, and visible
    the moment something precedes it on the line. Joining claims with ". " would hide every one
    of them.
    """
    claim = "Microsoft shipped a new tier"

    assert research.recommend_mode(claim)[0] == NONE
    assert research.recommend_mode(research_question(OPINION, [claim]))[0] == LIGHT


@pytest.mark.parametrize("idea", [OPINION, FACTUAL])
def test_adding_claims_can_add_a_signal_and_can_never_remove_one(idea):
    """What `plan_angle`'s raise-only re-resolution rests on.

    The question grows at the end, so every earlier line keeps its offsets and its sentence
    starts. A construction that prefixed or re-punctuated the idea would break this quietly.
    """
    before = set(research.recommend_mode(research_question(idea))[1])
    after = set(
        research.recommend_mode(
            research_question(idea, ["Microsoft shipped a new tier", "teams leave sooner"])
        )[1]
    )

    assert before <= after


def test_the_channel_and_the_audience_are_kept_out_of_the_question(session):
    """A floor that fires on every brief has stopped saying anything.

    "LinkedIn" is a capitalised name and "B2B" is an acronym: either in the question pins every
    brief to `light` for ever. This asserts the bland brief stays bland with both present.
    """
    llm = FakeLLM({**BRIEF_ANSWER, "audience": "B2B marketing leaders in SaaS"})
    brief = build_brief(session, llm, idea=OPINION, channel="LinkedIn")

    assert brief.research_mode == NONE
    assert research_question(brief.idea) == f"- {OPINION}"
