import logging
import re
from datetime import datetime

import pytest
from sqlmodel import select

from app.config import settings
from app.extraction import (
    APPROVE_AT_COVERAGE,
    MAX_HOOK_PROPOSALS,
    Cohort,
    ExtractionError,
    propose_hooks,
    uncovered_posts,
)
from app.models.generation_trace import GenerationTrace
from app.models.post import Post
from app.models.template import Template, TemplateKind, TemplateStatus
from app.templates import (
    create_template,
    edit_template,
    latest_versions,
    retire,
    usable_templates,
)


class FakeLLM:
    """Records the prompt it was given and replays a canned response."""

    def __init__(self, response: dict):
        self.response = response
        self.system: str | None = None
        self.user: str | None = None

    def complete_json(self, system: str, user: str, images=()) -> dict:
        self.system, self.user = system, user
        return self.response


TWO_HOOKS = {
    "hooks": [
        {
            "name": "transformation",
            "pattern": "{small_value} turned into {large_value} {unit}",
            "tone": "plain, lowercase, numbers first",
            "slots": [{"name": "small_value", "example": "$450"}],
            "source_post_ids": ["win-1"],
            "rationale": "leads with the delta",
        },
        {
            "name": "deadline",
            "pattern": "On {date}, {platform} will {change}",
            "tone": "urgent",
            "slots": [{"name": "date", "example": "July 27"}],
            "source_post_ids": ["win-2"],
        },
    ]
}


def hooks_citing(*ids: str) -> dict:
    """A one-proposal response whose provenance is exactly `ids`.

    Coverage is the approval gate now and `_to_template` drops a proposal that cites nothing
    it was shown, so a canned response naming ids the test never added is no longer a stored
    template with odd provenance — it is nothing at all, and every assertion over the result
    passes vacuously.
    """
    return {
        "hooks": [
            {"name": "cited", "pattern": "{a} became {b}", "source_post_ids": list(ids)}
        ]
    }


# Comfortably after `settings.voice_since`, so a post is current unless a test says so.
CURRENT = datetime(2026, 6, 1)

# The account other creators' posts are collected under.
INSPIRATION = settings.inspiration_account


def add_post(
    session,
    zid: str,
    engaged: int,
    content: str = "A hook line.\n\nBody text.",
    author: str | None = None,
    published_at: datetime | None = CURRENT,
) -> Post:
    post = Post(
        zernio_id=zid,
        platform="linkedin",
        content=content,
        engaged_actions=engaged,
        impressions=engaged * 30,
        account_username=author or settings.voice_account,
        published_at=published_at,
    )
    session.add(post)
    session.flush()
    return post


def test_a_proposal_below_the_coverage_threshold_becomes_a_proposed_template(session):
    # One post each, well under `APPROVE_AT_COVERAGE`. The status assertion below is only
    # true because of that — coverage at or above the threshold arrives APPROVED instead.
    add_post(session, "win-1", 185)
    add_post(session, "win-2", 139)

    proposals = propose_hooks(session, FakeLLM(TWO_HOOKS))

    assert len(proposals) == 2
    assert all(t.kind == TemplateKind.HOOK for t in proposals)
    assert all(t.status == TemplateStatus.PROPOSED for t in proposals)
    assert all(t.version == 1 for t in proposals)


def test_a_proposal_is_not_usable_until_approved(session):
    add_post(session, "win-1", 185)
    add_post(session, "win-2", 139)

    propose_hooks(session, FakeLLM(TWO_HOOKS))

    assert usable_templates(session, TemplateKind.HOOK) == []
    assert len(latest_versions(session, TemplateKind.HOOK)) == 2


def test_each_proposal_records_the_posts_it_came_from(session):
    add_post(session, "win-1", 185)
    add_post(session, "win-2", 139)

    proposals = propose_hooks(session, FakeLLM(TWO_HOOKS))

    by_name = {t.name: t for t in proposals}
    assert by_name["transformation"].provenance == ["win-1"]
    assert by_name["deadline"].provenance == ["win-2"]


def test_pattern_tone_and_slots_are_persisted(session):
    add_post(session, "win-1", 185)

    proposals = propose_hooks(session, FakeLLM(TWO_HOOKS))
    proposal = next(t for t in proposals if t.name == "transformation")

    assert proposal.body["pattern"] == "{small_value} turned into {large_value} {unit}"
    assert proposal.body["tone"] == "plain, lowercase, numbers first"
    assert proposal.slots == [{"name": "small_value", "example": "$450"}]


def test_the_model_sees_every_post_in_the_cohort_not_the_strongest_few(session):
    """The 12-post sample is what caused the defect this replaced.

    With nothing repeating among twelve posts the model had nothing to do but transcribe
    each one, and hook provenance averaged 1.09 posts per template. Twenty posts here, well
    past the old cutoff, so an implementation that kept any limit loses the weakest of them.
    """
    for index in range(20):
        add_post(session, f"p{index}", engaged=index, content=f"Hook number {index}.")

    llm = FakeLLM(TWO_HOOKS)
    propose_hooks(session, llm)

    assert [f"Hook number {i}." in llm.user for i in range(20)] == [True] * 20


def test_the_prompt_neither_ranks_the_posts_nor_shows_their_engagement(session):
    """Dropping `order_by` is not enough on its own.

    The old preamble said "strongest first" and "weight the top of this list most heavily",
    and the per-post line carried `engaged`. Left in place they would keep engagement
    steering extraction from the user message after SQL stopped doing it — one grep away
    from the `Never rank` rule and invisible to any test that only checks the query.
    """
    add_post(session, "win-1", 185)

    llm = FakeLLM(TWO_HOOKS)
    propose_hooks(session, llm)

    assert "win-1" in llm.user
    assert "185" not in llm.user
    assert "engaged" not in llm.user
    assert "strongest" not in llm.user.lower()


def test_extraction_is_scoped_to_one_platform(session):
    add_post(session, "li", 185)
    other = add_post(session, "tw", 900)
    other.platform = "twitter"
    session.add(other)
    session.flush()

    llm = FakeLLM(TWO_HOOKS)
    propose_hooks(session, llm, platform="linkedin")

    assert "li" in llm.user
    assert "tw" not in llm.user


def test_posts_without_content_are_not_offered_as_evidence(session):
    add_post(session, "empty", 500, content="   ")
    add_post(session, "win-1", 185)

    llm = FakeLLM(TWO_HOOKS)
    propose_hooks(session, llm)

    assert "empty" not in llm.user


def test_a_textless_post_is_dropped_and_every_real_post_still_reaches_the_model(session):
    # There is no sample slot to consume any more, but the exclusion still has to happen in
    # the query rather than by falling off the end of a limit that no longer exists.
    add_post(session, "empty", 500, content="   ")
    for index in range(3):
        add_post(session, f"real{index}", engaged=index, content=f"Hook number {index}.")

    llm = FakeLLM(TWO_HOOKS)
    propose_hooks(session, llm)

    assert "empty" not in llm.user
    assert [f"Hook number {i}." in llm.user for i in range(3)] == [True, True, True]


def test_only_the_voice_accounts_own_posts_shape_templates(session):
    # A creator post stays in the corpus as reference material, but the templates claim to
    # describe Monte's voice, so they cannot be led by someone else's writing.
    add_post(session, "creator", 1240, content="Someone else's hook.", author="External creator")
    add_post(session, "ours", 185, content="Monte's own hook.")

    llm = FakeLLM(TWO_HOOKS)
    propose_hooks(session, llm)

    assert "Monte's own hook." in llm.user
    assert "Someone else's hook." not in llm.user


def test_the_inspiration_cohort_reads_creator_posts_instead_of_montes(session):
    # A hook is a borrowable shape, so a creator's posts may teach one. The cohorts are
    # never mixed: whichever ranked higher would otherwise lead the sample.
    add_post(session, "theirs", 1240, content="Someone else's hook.", author=INSPIRATION)
    add_post(session, "ours", 185, content="Monte's own hook.")

    llm = FakeLLM(TWO_HOOKS)
    propose_hooks(session, llm, cohort=Cohort.INSPIRATION)

    assert "Someone else's hook." in llm.user
    assert "Monte's own hook." not in llm.user


def test_the_cohort_is_matched_by_value_so_a_plain_string_still_routes_correctly(session):
    """`Cohort` is a StrEnum, so `"voice" == Cohort.VOICE` while `"voice" is Cohort.VOICE`
    is False. An identity check would send any caller passing the bare string — a value
    that compares equal everywhere else — silently down the *inspiration* branch, and
    Monte's voice templates would quietly be built from other people's writing.
    """
    add_post(session, "theirs", 1240, content="Someone else's hook.", author=INSPIRATION)
    add_post(session, "ours", 185, content="Monte's own hook.")

    llm = FakeLLM(TWO_HOOKS)
    propose_hooks(session, llm, cohort="voice")  # type: ignore[arg-type]

    assert "Monte's own hook." in llm.user
    assert "Someone else's hook." not in llm.user


def test_the_voice_era_floor_does_not_gate_inspiration_posts(session):
    # `voice_since` marks where Monte's current era begins. A creator's posts are never
    # ranked against his, and his era says nothing about their timeline.
    add_post(
        session,
        "theirs",
        900,
        content="An older creator hook.",
        author=INSPIRATION,
        published_at=datetime(2024, 6, 1),
    )

    llm = FakeLLM(TWO_HOOKS)
    propose_hooks(session, llm, cohort=Cohort.INSPIRATION)

    assert "An older creator hook." in llm.user


def test_a_textless_inspiration_post_is_not_evidence(session):
    # At least one collected creator post is an image with no text at all. It carries no
    # hook, so it is no evidence however the sample is drawn.
    add_post(session, "image-only", 2000, content="   ", author=INSPIRATION)
    add_post(session, "theirs", 185, content="Someone else's hook.", author=INSPIRATION)

    llm = FakeLLM(TWO_HOOKS)
    propose_hooks(session, llm, cohort=Cohort.INSPIRATION)

    assert "Someone else's hook." in llm.user
    assert "image-only" not in llm.user


def test_an_excluded_inspiration_post_is_not_evidence(session):
    held_out = add_post(session, "viral", 5000, content="A one-off.", author=INSPIRATION)
    held_out.excluded_from_extraction = True
    session.add(held_out)
    session.flush()
    add_post(session, "theirs", 185, content="Someone else's hook.", author=INSPIRATION)

    llm = FakeLLM(TWO_HOOKS)
    propose_hooks(session, llm, cohort=Cohort.INSPIRATION)

    assert "Someone else's hook." in llm.user
    assert "A one-off." not in llm.user


def test_a_hook_records_the_cohort_it_was_extracted_from(session):
    # "Borrowed from a creator" has to stay distinguishable from "proven in Monte's own
    # posts" long after the extraction run.
    add_post(session, "ours", 185)
    add_post(session, "theirs", 185, author=INSPIRATION)

    voice = propose_hooks(session, FakeLLM(hooks_citing("ours")))
    borrowed = propose_hooks(
        session, FakeLLM(hooks_citing("theirs")), cohort=Cohort.INSPIRATION
    )

    assert {t.body["cohort"] for t in voice} == {"voice"}
    assert {t.body["cohort"] for t in borrowed} == {"inspiration"}


def test_extraction_reads_the_voice_cohort_unless_told_otherwise(session):
    add_post(session, "ours", 185, content="Monte's own hook.")
    add_post(session, "theirs", 1240, content="Someone else's hook.", author=INSPIRATION)

    llm = FakeLLM(hooks_citing("ours"))
    proposals = propose_hooks(session, llm)

    assert "Monte's own hook." in llm.user
    assert "Someone else's hook." not in llm.user
    assert [t.body["cohort"] for t in proposals] == ["voice"]


def test_a_post_excluded_from_extraction_is_not_evidence(session):
    strongest = add_post(session, "launch", 500, content="Breaking out of stealth.")
    strongest.excluded_from_extraction = True
    session.add(strongest)
    session.flush()
    add_post(session, "win-1", 185, content="A repeatable hook.")

    llm = FakeLLM(TWO_HOOKS)
    propose_hooks(session, llm)

    assert "A repeatable hook." in llm.user
    assert "Breaking out of stealth." not in llm.user


def test_a_post_from_before_the_voice_era_is_not_evidence(session):
    # The corpus reaches back further than the voice does. The old posts are a different
    # genre — AI-industry commentary rather than the Amazon-listing work Pixii publishes
    # now — so templates drawn from them would describe a voice the company has left.
    stale = datetime(2024, 6, 1)
    add_post(session, "old", 900, content="Sora just changed everything.", published_at=stale)
    add_post(session, "win-1", 185, content="A repeatable hook.")

    llm = FakeLLM(TWO_HOOKS)
    propose_hooks(session, llm)

    assert "A repeatable hook." in llm.user
    assert "Sora just changed everything." not in llm.user


def test_an_undated_post_is_not_evidence(session):
    # Nothing shows an undated post is current, and the whole point of the floor is
    # currency, so it cannot be allowed to pass by default.
    add_post(session, "undated", 900, content="No date on this one.", published_at=None)
    add_post(session, "win-1", 185, content="A repeatable hook.")

    llm = FakeLLM(TWO_HOOKS)
    propose_hooks(session, llm)

    assert "A repeatable hook." in llm.user
    assert "No date on this one." not in llm.user


def test_a_pre_era_post_is_dropped_and_every_current_post_still_reaches_the_model(session):
    # The era floor has to stay in the query. Nothing else removes it now that there is no
    # limit for a pre-era post to fall off the end of.
    stale = datetime(2024, 6, 1)
    add_post(session, "old", 500, content="Sora just changed everything.", published_at=stale)
    for index in range(3):
        add_post(session, f"real{index}", engaged=index, content=f"Hook number {index}.")

    llm = FakeLLM(TWO_HOOKS)
    propose_hooks(session, llm)

    assert "Sora just changed everything." not in llm.user
    assert [f"Hook number {i}." in llm.user for i in range(3)] == [True, True, True]


def test_an_excluded_post_stays_in_the_corpus(session):
    post = add_post(session, "launch", 500)
    post.excluded_from_extraction = True
    session.add(post)
    session.flush()

    assert session.get(Post, post.id) is not None


def test_an_empty_corpus_produces_no_proposals_and_does_not_call_the_model(session):
    llm = FakeLLM(TWO_HOOKS)

    assert propose_hooks(session, llm) == []
    assert llm.user is None


def test_a_response_without_hooks_is_an_error(session):
    add_post(session, "win-1", 185)

    with pytest.raises(ExtractionError):
        propose_hooks(session, FakeLLM({"something_else": []}))


def test_a_proposal_missing_its_pattern_is_rejected(session):
    add_post(session, "win-1", 185)

    assert propose_hooks(session, FakeLLM({"hooks": [{"name": "patternless"}]})) == []
    # The rejection has to be a *drop*, not a silently-stored template with an empty
    # pattern — generation would render that as nothing at all.
    assert latest_versions(session, TemplateKind.HOOK) == []


def test_a_proposal_missing_its_pattern_does_not_cost_its_siblings(session, caplog):
    """One unusable proposal out of three used to return a 502 and write nothing at all.

    The unusable one is *first* on purpose: with it last, an implementation that `break`s
    out of the loop instead of continuing past the rejection still passes.
    """
    add_post(session, "win-1", 185)
    add_post(session, "win-2", 139)
    batch = {"hooks": [{"name": "patternless"}, *TWO_HOOKS["hooks"]]}

    with caplog.at_level(logging.WARNING, logger="app.extraction"):
        kept = propose_hooks(session, FakeLLM(batch))

    assert [t.name for t in kept] == ["transformation", "deadline"]
    assert [t.name for t in latest_versions(session, TemplateKind.HOOK)] == [
        "deadline",
        "transformation",
    ]
    # The type, not just the message: the catch around `_to_template` is deliberately broad,
    # so a malformed proposal and a real bug inside it read identically without it.
    assert "_RejectedProposal" in caplog.text


def test_a_hook_proposal_that_is_not_an_object_does_not_cost_its_siblings(session):
    """A bare string reaches `_to_template.get` as an AttributeError, not a rejection.

    Covered separately from the blank-pattern case because it is the exception type a
    narrower catch would miss — the failure `propose_visuals` records paying for twice.
    """
    add_post(session, "win-1", 185)
    add_post(session, "win-2", 139)
    batch = {"hooks": ["not an object", *TWO_HOOKS["hooks"]]}

    assert [t.name for t in propose_hooks(session, FakeLLM(batch))] == [
        "transformation",
        "deadline",
    ]


# --- coverage decides the status --------------------------------------------------------


def _corpus(session, count: int) -> list[str]:
    ids = [f"post-{i}" for i in range(count)]
    for index, zid in enumerate(ids):
        add_post(session, zid, engaged=index, content=f"Hook number {index}.")
    return ids


def test_a_pattern_covering_the_threshold_arrives_approved(session):
    """Auto-approval on evidence, which is the point of counting coverage at all.

    A human gate with a backlog protects nothing — 48 unreviewed proposals are sitting in
    Studio to prove it. What the gate protected, that nothing is lost, is bought by the
    append-only write instead.
    """
    ids = _corpus(session, APPROVE_AT_COVERAGE)

    (hook,) = propose_hooks(session, FakeLLM(hooks_citing(*ids)))

    assert len(hook.provenance) == APPROVE_AT_COVERAGE
    assert hook.status is TemplateStatus.APPROVED
    assert [t.name for t in usable_templates(session, TemplateKind.HOOK)] == ["cited"]


def test_a_pattern_one_short_of_the_threshold_arrives_proposed(session):
    """One below, not zero: a test at coverage 1 passes against `>= 1` too."""
    ids = _corpus(session, APPROVE_AT_COVERAGE)

    (hook,) = propose_hooks(session, FakeLLM(hooks_citing(*ids[:-1])))

    assert len(hook.provenance) == APPROVE_AT_COVERAGE - 1
    assert hook.status is TemplateStatus.PROPOSED
    assert usable_templates(session, TemplateKind.HOOK) == []


def test_coverage_is_counted_off_the_filtered_provenance_not_the_models_claim(session):
    """Invented ids must not buy approval, and they are measured rather than feared.

    All three VISUAL provenance citations in the database today join to no post row. With
    coverage as the approval gate, counting `len(proposal["source_post_ids"])` instead of
    the filtered list would let five invented ids auto-approve a template covering nothing.
    """
    ids = _corpus(session, APPROVE_AT_COVERAGE - 1)
    claimed = [*ids, "never-collected"]

    (hook,) = propose_hooks(session, FakeLLM(hooks_citing(*claimed)))

    assert len(claimed) == APPROVE_AT_COVERAGE
    assert hook.provenance == ids
    assert hook.status is TemplateStatus.PROPOSED


def test_a_proposal_citing_nothing_it_was_shown_is_dropped(session):
    """`source_post_ids` is required by the 2.0.0 schema, so the code has to require it too.

    A pattern grounded in no post the model saw covers nothing, and storing it would leave a
    zero-coverage template in the library that the schema says cannot exist.
    """
    add_post(session, "win-1", 185)

    assert propose_hooks(session, FakeLLM(hooks_citing("invented"))) == []
    assert propose_hooks(session, FakeLLM({"hooks": [{"name": "n", "pattern": "{a}"}]})) == []
    assert latest_versions(session, TemplateKind.HOOK) == []


def test_no_more_than_ten_patterns_are_stored_and_the_rest_are_logged(session, caplog):
    """The prompt asks for at most 10; this is what makes that a fact rather than a hope.

    Minting a family per proposal is what put 42 hook families in the database, and an
    unbounded batch is that defect with the whole corpus behind it.
    """
    (zid,) = _corpus(session, 1)
    batch = {
        "hooks": [
            {"name": f"h{i}", "pattern": "{a} became {b}", "source_post_ids": [zid]}
            for i in range(MAX_HOOK_PROPOSALS + 3)
        ]
    }

    with caplog.at_level(logging.WARNING, logger="app.extraction"):
        kept = propose_hooks(session, FakeLLM(batch))

    assert [t.name for t in kept] == [f"h{i}" for i in range(MAX_HOOK_PROPOSALS)]
    assert len(latest_versions(session, TemplateKind.HOOK)) == MAX_HOOK_PROPOSALS
    # Never silently: a truncated batch that reads as a complete one is how a partial answer
    # gets mistaken for the whole picture. The whole message, not `"13" in caplog.text` —
    # `caplog.text` carries `extraction.py:NNN`, so a bare number can pass off the line
    # number the day the warning moves, which is a test that has stopped checking anything.
    assert (
        f"model returned {MAX_HOOK_PROPOSALS + 3} hooks; "
        f"considered the first {MAX_HOOK_PROPOSALS}" in caplog.text
    )


# --- reconciling into the families that already exist -------------------------------------

# The line `_build_prompt` writes for each family it offers. The tests below read the ids back
# out of the prompt rather than knowing them in advance, so a prompt that stops naming them
# fails here instead of quietly minting a sibling family per run.
_OFFERED_FAMILY = re.compile(r"family_id: (\S+)")


class ReconcilingLLM(FakeLLM):
    """Cites whichever family the prompt offered, which is what a real model is asked to do.

    Deliberately not a canned `family_id`. A fake that always cites an id the test chose would
    pass just as happily against a prompt that never listed the families — and listing them is
    half of what stops the family count growing. Reading it back out of the user message makes
    the two-run test below cover the prompt and the write together.
    """

    def __init__(self, source_post_ids: list[str]):
        super().__init__({})
        self.source_post_ids = source_post_ids

    def complete_json(self, system: str, user: str, images=()) -> dict:
        super().complete_json(system, user, images)
        proposal = {
            "name": "cited",
            "pattern": "{a} became {b}",
            "source_post_ids": self.source_post_ids,
        }
        offered = _OFFERED_FAMILY.findall(user)
        if offered:
            proposal["family_id"] = offered[0]
        return {"hooks": [proposal]}


def hooks_citing_family(family_id: str, *ids: str) -> dict:
    """`hooks_citing`, with the proposal claiming to be a revision of an existing family."""
    response = hooks_citing(*ids)
    response["hooks"][0]["family_id"] = family_id
    return response


def existing_family(
    session,
    *,
    name: str = "cited",
    provenance: list[str] | None = None,
    status: TemplateStatus = TemplateStatus.PROPOSED,
) -> Template:
    """A hook family a previous run left behind, at version 1."""
    return create_template(
        session,
        kind=TemplateKind.HOOK,
        name=name,
        body={"pattern": "{a} became {b}"},
        provenance=provenance or [],
        status=status,
    )


def test_a_second_extraction_over_an_unchanged_corpus_adds_no_families(session):
    """The headline. 42 hook families came out of roughly 12 posts because nothing ever
    looked an existing template up: every proposal called `create_template`, which mints
    `family_id=uuid4().hex` unconditionally.

    Two runs, one corpus, one fake that behaves the way the prompt asks a model to.
    """
    ids = _corpus(session, 3)
    llm = ReconcilingLLM(ids)

    (first,) = propose_hooks(session, llm)
    (second,) = propose_hooks(session, llm)

    assert first.family_id == second.family_id
    assert (first.version, second.version) == (1, 2)
    assert len(latest_versions(session, TemplateKind.HOOK)) == 1


def test_a_cited_family_gains_a_version_rather_than_a_sibling(session):
    ids = _corpus(session, 2)
    # A different name from the one the proposal carries, on purpose: the model's name wins,
    # which is the plain reading of "write version 2 from this proposal". Asserted rather than
    # left to chance because a hook's *name* is what `_structure_prompt` lists for
    # `compatible_hooks` — so this is a decision another file reads, not a cosmetic one.
    family = existing_family(session, name="older-name", provenance=[ids[0]])

    (revised,) = propose_hooks(session, FakeLLM(hooks_citing_family(family.family_id, *ids)))

    assert (revised.family_id, revised.version) == (family.family_id, 2)
    assert revised.provenance == ids
    assert revised.name == "cited"
    assert len(latest_versions(session, TemplateKind.HOOK)) == 1
    # Append-only: version 1 still records the name and the provenance it was written with,
    # so what the earlier run actually saw survives the revision.
    assert (family.name, family.provenance) == ("older-name", [ids[0]])


def test_the_prompt_offers_the_families_that_already_have_names(session):
    ids = _corpus(session, 1)
    family = existing_family(session, name="transformation", provenance=ids)

    llm = FakeLLM(hooks_citing(*ids))
    propose_hooks(session, llm)

    assert f"family_id: {family.family_id}" in llm.user
    # The name as well as the id: an id alone is unreadable, and the model has to be able to
    # tell whether its pattern is the same *shape* as this one.
    assert "transformation" in llm.user


def test_an_unrecognised_family_id_mints_a_new_family(session):
    """A cited id that names nothing is a naming failure, not a reason to lose the pattern.

    Deliberate, and the alternative was considered: dropping the proposal would discard a
    pattern with real coverage over a field the model invented. A duplicate family can be
    merged by a later run citing the right id; a dropped one is gone until the next run.
    """
    ids = _corpus(session, 2)

    (hook,) = propose_hooks(session, FakeLLM(hooks_citing_family("no-such-family", *ids)))

    assert hook.family_id != "no-such-family"
    assert hook.version == 1
    assert hook.provenance == ids


def test_a_proposed_family_whose_coverage_has_crossed_the_floor_is_promoted(session):
    """`edit_template` copies the status of the row it revises (`templates.py:69`).

    Without an explicit promotion a family that landed at coverage 3 stays PROPOSED forever,
    even when a later run finds it covering forty posts — and coverage growing as posts
    arrive is the entire point of extracting over the whole corpus.
    """
    ids = _corpus(session, APPROVE_AT_COVERAGE)
    family = existing_family(session, provenance=[ids[0]])

    (revised,) = propose_hooks(session, FakeLLM(hooks_citing_family(family.family_id, *ids)))

    assert revised.version == 2
    assert revised.status is TemplateStatus.APPROVED
    assert [t.family_id for t in usable_templates(session, TemplateKind.HOOK)] == [
        family.family_id
    ]


def test_a_revision_one_short_of_the_floor_is_still_proposed(session):
    """One below, not zero: a test at coverage 1 passes against `>= 1` too."""
    ids = _corpus(session, APPROVE_AT_COVERAGE)
    family = existing_family(session, provenance=[ids[0]])

    (revised,) = propose_hooks(
        session, FakeLLM(hooks_citing_family(family.family_id, *ids[:-1]))
    )

    assert revised.status is TemplateStatus.PROPOSED
    assert usable_templates(session, TemplateKind.HOOK) == []


def test_an_approved_family_whose_coverage_has_fallen_keeps_its_approval(session):
    """Nothing is ever auto-demoted. A run that finds a pattern covering less than it did
    is evidence about that run, not grounds for taking a template out of the library."""
    ids = _corpus(session, APPROVE_AT_COVERAGE)
    family = existing_family(session, provenance=ids, status=TemplateStatus.APPROVED)

    (revised,) = propose_hooks(
        session, FakeLLM(hooks_citing_family(family.family_id, ids[0]))
    )

    assert revised.provenance == [ids[0]]
    assert revised.status is TemplateStatus.APPROVED


def test_a_second_proposal_citing_one_family_does_not_undo_the_first_ones_promotion(session):
    """Two proposals citing the same family, inside one batch, high coverage first.

    The write the loop makes has to be visible to the rest of the batch. Against a snapshot
    taken before the loop, the second proposal revises the *old* PROPOSED row — and
    `edit_template` copies that row's status — so version 3 lands PROPOSED on top of the
    APPROVED version 2 the first proposal just earned. That is an auto-demotion.
    """
    ids = _corpus(session, APPROVE_AT_COVERAGE)
    family = existing_family(session, provenance=[ids[0]])
    batch = {
        "hooks": [
            {
                "name": "broad",
                "pattern": "{a} became {b}",
                "family_id": family.family_id,
                "source_post_ids": ids,
            },
            {
                "name": "narrow",
                "pattern": "{a} became {b}",
                "family_id": family.family_id,
                "source_post_ids": ids[:1],
            },
        ]
    }

    kept = propose_hooks(session, FakeLLM(batch))

    assert [t.version for t in kept] == [2, 3]
    (latest,) = latest_versions(session, TemplateKind.HOOK)
    assert (latest.version, latest.status) == (3, TemplateStatus.APPROVED)


def test_a_proposal_citing_a_retired_family_is_dropped_and_mints_nothing(session, caplog):
    """A family a human deliberately retired is never silently revived — and never re-minted
    under a new id either, which is what a `create_template` fallback on the edit path would
    quietly do. No branch handles this: `edit_template` raises `RetiredTemplateError` and the
    per-proposal catch logs it and moves on.

    The retired citation is *first* in the batch, the convention the blank-pattern test above
    sets. It is the only rejection in this path raised from inside a function that touches the
    session, so "the session is still usable afterwards" is the part worth checking rather
    than reasoning about.
    """
    ids = _corpus(session, APPROVE_AT_COVERAGE)
    family = existing_family(session, provenance=[ids[0]])
    retire(session, family)
    batch = hooks_citing_family(family.family_id, *ids)
    batch["hooks"].append({"name": "new", "pattern": "{a}", "source_post_ids": ids})

    with caplog.at_level(logging.WARNING, logger="app.extraction"):
        kept = propose_hooks(session, FakeLLM(batch))

    assert [t.name for t in kept] == ["new"]
    still = next(t for t in latest_versions(session, TemplateKind.HOOK) if t.name != "new")
    assert (still.family_id, still.version) == (family.family_id, 1)
    assert still.status is TemplateStatus.RETIRED
    # Two families, not three: the retired one and the sibling that survived it. Nothing was
    # re-minted under the model's own name for the retired shape.
    assert len(latest_versions(session, TemplateKind.HOOK)) == 2
    assert "RetiredTemplateError" in caplog.text


def test_a_retired_family_is_not_offered_for_reconciliation(session):
    """It is in the lookup — that is what makes the drop above possible — and not in the
    prompt. `usable_templates` draws the same line: a withdrawn template is not offered."""
    ids = _corpus(session, 1)
    family = existing_family(session, name="withdrawn", provenance=ids)
    retire(session, family)

    llm = ReconcilingLLM(ids)
    propose_hooks(session, llm)

    assert family.family_id not in llm.user
    assert "withdrawn" not in llm.user


def test_a_cited_family_whose_citations_all_fall_away_is_dropped_not_revised(session):
    """Empty filtered provenance is checked *before* the family is looked up, and the order
    is deliberate. Reconciling first would write a version 2 that overwrites the family's
    real provenance with an empty one; dropping the proposal costs that family one version,
    which the next run recovers.
    """
    ids = _corpus(session, 2)
    family = existing_family(session, provenance=ids)

    kept = propose_hooks(
        session, FakeLLM(hooks_citing_family(family.family_id, "never-collected"))
    )

    assert kept == []
    (still,) = latest_versions(session, TemplateKind.HOOK)
    assert (still.version, still.provenance) == (1, ids)


# --- the calls extraction makes are recorded ------------------------------------------------


def test_the_hook_proposal_writes_a_trace_naming_the_registered_prompt(session):
    """Extraction was the last stage whose model calls left no record at all.

    It matters more here than the name "audit trail" suggests: a human approves what this call
    proposes, and every draft generated afterwards is generated *from* that template. "Which
    prompt, at which version, proposed this" is therefore a lineage question — the same kind
    `(family_id, version)` answers one layer down — and it had no answer.
    """
    add_post(session, "win-1", 185)

    propose_hooks(session, FakeLLM(TWO_HOOKS))
    session.flush()

    (trace,) = session.exec(select(GenerationTrace)).all()
    assert (trace.prompt_name, trace.prompt_version) == ("extraction.hooks", "2.0.0")
    assert trace.input_artifact_ids["posts"] == "win-1"
    assert trace.input_artifact_ids["cohort"] == "voice"
    assert trace.output_hash is not None and trace.error is None
    # Never claimed, because nothing at this layer has seen a token count.
    assert (trace.prompt_tokens, trace.completion_tokens) == (None, None)


def test_the_trace_records_which_families_the_model_was_allowed_to_cite(session):
    """A hook that gained a version gained it because the prompt named its family.

    The same reason `propose_structures` records the hook list it showed: which families were
    offered is part of how a revision came about, and `(family_id, version)` is what identifies
    them — a bare id would not say which version the model was looking at.
    """
    ids = _corpus(session, 1)
    family = existing_family(session, provenance=ids)

    propose_hooks(session, FakeLLM(hooks_citing(*ids)))
    session.flush()

    (trace,) = session.exec(select(GenerationTrace)).all()
    assert trace.input_artifact_ids["families"] == f"{family.family_id}/1"


def test_a_failed_extraction_call_is_recorded_with_its_error(session):
    class Broken:
        def complete_json(self, system, user, images=()):
            raise RuntimeError("vision endpoint unavailable")

    add_post(session, "win-1", 185)

    with pytest.raises(RuntimeError):
        propose_hooks(session, Broken())
    session.flush()

    (trace,) = session.exec(select(GenerationTrace)).all()
    assert trace.error == "RuntimeError: vision endpoint unavailable"
    assert trace.output_hash is None
    assert trace.latency_ms is not None


# --- The uncovered set: what the live library speaks for, and what it does not ---------------
#
# The first real corpus-wide run produced 19 templates citing 92 distinct posts and left 144 of
# 236 uncovered. Whether that is because those posts share no shape, or because a model handed
# 274 posts at once stopped labelling early, is only answerable by running over the leftovers
# and seeing what comes back.


def test_a_post_the_newest_version_dropped_is_uncovered_again(session):
    """Counting every row would hide exactly the gap this exists to show.

    A v1 that cited a post and a v2 that dropped it means the *current* library does not cover
    that post. Resolving through `latest_versions` is what makes that true; a query over every
    template row credits the superseded v1 and reports the post as covered — the
    `(family_id, version)` mistake this codebase has already paid for several times.
    """
    ids = _corpus(session, 3)
    family = existing_family(session, provenance=[ids[0]])
    edit_template(session, family, provenance=[ids[1]])

    left = {post.zernio_id for post in uncovered_posts(session, TemplateKind.HOOK)}

    assert left == {ids[0], ids[2]}


def test_a_retired_familys_citations_do_not_cover_anything(session):
    """A withdrawn template is not the library speaking for a post.

    Same set `propose_hooks` calls `offered`: newest version, minus RETIRED. Counting a retired
    family's provenance would report a post as covered by something generation may not use.
    """
    ids = _corpus(session, 2)
    retire(session, existing_family(session, provenance=[ids[0]]))

    left = {post.zernio_id for post in uncovered_posts(session, TemplateKind.HOOK)}

    assert left == set(ids)


def test_coverage_is_asked_per_kind(session):
    """A hook citing a post says nothing about whether a structure describes it."""
    ids = _corpus(session, 2)
    existing_family(session, provenance=ids)

    assert uncovered_posts(session, TemplateKind.HOOK) == []
    assert {p.zernio_id for p in uncovered_posts(session, TemplateKind.STRUCTURE)} == set(ids)


def test_an_uncovered_run_shows_the_model_only_the_posts_nothing_covers(session):
    ids = _corpus(session, 3)
    existing_family(session, provenance=[ids[0], ids[1]])

    llm = FakeLLM(hooks_citing(ids[2]))
    propose_hooks(session, llm, uncovered_only=True)

    # The prompt, not the return value: a flag that narrowed nothing would still return one
    # template here, because the canned proposal cites a post that is in either sample.
    assert "Hook number 2." in llm.user
    assert "Hook number 0." not in llm.user
    assert "Hook number 1." not in llm.user


def test_an_empty_uncovered_set_returns_no_proposals_and_does_not_call_the_model(session):
    """The loop terminating normally, and the state the operator is trying to reach.

    Not an error: nothing is left for extraction to look at, which is the answer. Asserted on
    the model never being called as well as on the empty list — a run that paid for a
    completion over an empty sample would still return `[]`.
    """
    ids = _corpus(session, 2)
    existing_family(session, provenance=ids)

    llm = FakeLLM(hooks_citing(*ids))

    assert propose_hooks(session, llm, uncovered_only=True) == []
    assert llm.user is None


def test_the_uncovered_count_falls_after_a_run_that_covers_new_posts(session):
    """The number has to move, or it is decoration on a page beside a button."""
    ids = _corpus(session, 3)
    assert len(uncovered_posts(session, TemplateKind.HOOK)) == 3

    propose_hooks(session, FakeLLM(hooks_citing(ids[0], ids[1])))

    assert {p.zernio_id for p in uncovered_posts(session, TemplateKind.HOOK)} == {ids[2]}
