import logging
import re
from datetime import datetime

import pytest
from sqlmodel import select

from app.config import settings
from app.extraction import (
    APPROVE_AT_COVERAGE,
    MAX_STRUCTURE_PROPOSALS,
    Cohort,
    ExtractionError,
    compatible_hooks,
    propose_structures,
)
from app.models.generation_trace import GenerationTrace
from app.models.post import Post
from app.models.template import Template, TemplateKind, TemplateStatus
from app.templates import (
    approve,
    create_template,
    edit_template,
    latest_versions,
    retire,
    usable_templates,
)


class FakeLLM:
    def __init__(self, response: dict):
        self.response = response
        self.user: str | None = None

    def complete_json(self, system: str, user: str, images=()) -> dict:
        self.user = user
        return self.response


OFFER_REWARD = {
    "structures": [
        {
            "name": "offer-reward",
            "post_type": "offer-reward",
            "sections": [
                {"name": "hook", "guidance": "Lead with the outcome and its price."},
                {"name": "proof", "guidance": "Show the artefact. Concrete numbers only."},
                {"name": "reward", "guidance": "Name the free resource and how to claim it."},
            ],
            "source_post_ids": ["win-1"],
        }
    ]
}


def structures_citing(*ids: str, hooks: tuple[str, ...] = ()) -> dict:
    """A one-proposal response whose provenance is exactly `ids`.

    Coverage is the approval gate now and `_to_structure` drops a proposal that cites nothing
    it was shown, so a canned response naming ids the test never added is no longer a stored
    structure with odd provenance — it is nothing at all, and every assertion over the result
    passes vacuously. `hooks` are hook *family ids*, never names: see the rename test below.
    """
    return {
        "structures": [
            {
                **OFFER_REWARD["structures"][0],
                "source_post_ids": list(ids),
                "compatible_hooks": list(hooks),
            }
        ]
    }


def add_post(session, zid: str, engaged: int, content: str) -> Post:
    post = Post(
        zernio_id=zid,
        platform="linkedin",
        content=content,
        engaged_actions=engaged,
        account_username=settings.voice_account,
        # Comfortably after `settings.voice_since`, so the post counts as current.
        published_at=datetime(2026, 6, 1),
    )
    session.add(post)
    session.flush()
    return post


def a_hook(session, name: str, *, approved: bool = True):
    hook = create_template(
        session, kind=TemplateKind.HOOK, name=name, body={"pattern": "{a} = {b}"}
    )
    if approved:
        approve(session, hook)
    return hook


def test_creates_a_proposed_structure_with_its_sections_in_order(session):
    add_post(session, "win-1", 185, "Hook line.\n\nProof body.\n\nComment WORD for the doc.")

    structures = propose_structures(session, FakeLLM(OFFER_REWARD))

    assert len(structures) == 1
    structure = structures[0]
    assert structure.kind == TemplateKind.STRUCTURE
    assert structure.status == TemplateStatus.PROPOSED
    assert [s["name"] for s in structure.body["sections"]] == ["hook", "proof", "reward"]


def test_each_section_carries_its_own_guidance(session):
    add_post(session, "win-1", 185, "Body.")

    structure = propose_structures(session, FakeLLM(OFFER_REWARD))[0]

    assert structure.body["sections"][1]["guidance"].startswith("Show the artefact")


def test_the_post_type_is_recorded(session):
    add_post(session, "win-1", 185, "Body.")

    structure = propose_structures(session, FakeLLM(OFFER_REWARD))[0]

    assert structure.body["post_type"] == "offer-reward"


def test_compatible_hooks_are_resolved_to_families_that_exist(session):
    hook = a_hook(session, "ai-time-value-equation")
    add_post(session, "win-1", 185, "Body.")
    cited = structures_citing("win-1", hooks=(hook.family_id, "does-not-exist"))

    structure = propose_structures(session, FakeLLM(cited))[0]

    assert structure.body["compatible_hook_families"] == [hook.family_id]


def test_a_cited_hook_family_that_does_not_exist_is_dropped(session):
    hook = a_hook(session, "ai-time-value-equation")
    add_post(session, "win-1", 185, "Body.")
    cited = structures_citing("win-1", hooks=(hook.family_id, "does-not-exist"))

    structure = propose_structures(session, FakeLLM(cited))[0]

    assert len(structure.body["compatible_hook_families"]) == 1


def test_selecting_a_structure_surfaces_its_compatible_hooks(session):
    hook = a_hook(session, "ai-time-value-equation")
    add_post(session, "win-1", 185, "Body.")
    cited = structures_citing("win-1", hooks=(hook.family_id,))
    structure = propose_structures(session, FakeLLM(cited))[0]

    assert [h.id for h in compatible_hooks(session, structure)] == [hook.id]


def test_an_unapproved_compatible_hook_is_not_surfaced(session):
    hook = a_hook(session, "ai-time-value-equation", approved=False)
    add_post(session, "win-1", 185, "Body.")
    cited = structures_citing("win-1", hooks=(hook.family_id,))
    structure = propose_structures(session, FakeLLM(cited))[0]

    assert compatible_hooks(session, structure) == []


def test_a_structure_naming_no_hooks_surfaces_none(session):
    add_post(session, "win-1", 185, "Body.")
    structure = create_template(
        session, kind=TemplateKind.STRUCTURE, name="bare", body={"sections": []}
    )

    assert compatible_hooks(session, structure) == []


def test_the_model_sees_whole_posts_not_just_their_openings(session):
    body = "Opening line.\n\n" + ("Middle paragraph that carries the argument. " * 8) + "\n\nClose."
    add_post(session, "win-1", 185, body)

    llm = FakeLLM(OFFER_REWARD)
    propose_structures(session, llm)

    assert "Middle paragraph that carries the argument." in llm.user
    assert "Close." in llm.user


def test_the_model_sees_every_post_in_the_cohort_not_the_strongest_few(session):
    """Twenty posts, well past the twelve `sample_size` used to allow.

    This is the assertion the old `test_the_strongest_posts_lead_the_prompt` was replaced by,
    and it is deliberately not its inverse. Asserting that the strong post no longer comes
    first would pass against a sample that still drops the weak tail — which is the defect,
    not the ordering. What matters is that nothing falls off the end any more.
    """
    for index in range(20):
        add_post(session, f"p{index}", index, f"Post body number {index}.")

    llm = FakeLLM(structures_citing("p0"))
    propose_structures(session, llm)

    assert [f"Post body number {i}." in llm.user for i in range(20)] == [True] * 20


def test_the_prompt_neither_ranks_the_posts_nor_shows_their_engagement(session):
    """Dropping `order_by` is not enough on its own — the words carried the ranking too.

    The old preamble said "strongest first" and "weight the top of this list most heavily",
    and the per-post line carried `engaged`. Left in place they would keep engagement
    steering extraction from the user message after SQL stopped doing it.

    **Called with `focus`, and that is the point of this test rather than an extra.** The
    ranking language lived in two branches: the preamble every run sends, and the focus
    paragraph's "even if they are not the strongest performers". A version of this test that
    left `focus` empty would pass with that second phrase still in the prompt.
    """
    add_post(session, "win-1", 185, "Body.")

    llm = FakeLLM(structures_citing("win-1"))
    propose_structures(session, llm, focus="deep-research")

    assert "win-1" in llm.user
    assert "185" not in llm.user
    assert "engaged" not in llm.user
    assert "strongest" not in llm.user.lower()


def test_a_focus_asks_the_model_for_that_post_type_specifically(session):
    """`focus` survives `sample_size`'s deletion — naming a post type is still a real request.

    It was the only reason the docstring gave for widening the sample, so the two read as one
    feature; they are not. Whole-corpus extraction already shows the model every post of the
    type, and `focus` is what makes it describe *that* one.
    """
    add_post(session, "win-1", 185, "Body.")

    llm = FakeLLM(structures_citing("win-1"))
    propose_structures(session, llm, focus="deep-research")

    assert "deep-research" in llm.user


def test_an_empty_corpus_produces_nothing_and_does_not_call_the_model(session):
    llm = FakeLLM(OFFER_REWARD)

    assert propose_structures(session, llm) == []
    assert llm.user is None


def test_a_response_without_structures_is_an_error(session):
    add_post(session, "win-1", 185, "Body.")

    with pytest.raises(ExtractionError):
        propose_structures(session, FakeLLM({"hooks": []}))


def test_a_structure_without_sections_is_rejected(session):
    add_post(session, "win-1", 185, "Body.")

    hollow = {"structures": [{"name": "hollow", "sections": []}]}

    assert propose_structures(session, FakeLLM(hollow)) == []
    # A drop, not a stored structure with no sections: generation walks `sections`, so an
    # empty one is a template that produces an empty draft.
    assert latest_versions(session, TemplateKind.STRUCTURE) == []


def test_a_structure_without_sections_does_not_cost_its_siblings(session, caplog):
    """One unusable proposal used to return a 502 from the extract route and write nothing.

    The unusable one is *first* on purpose: with it last, an implementation that `break`s
    out of the loop instead of continuing past the rejection still passes.
    """
    add_post(session, "win-1", 185, "Body.")
    batch = {"structures": [{"name": "hollow", "sections": []}, *OFFER_REWARD["structures"]]}

    with caplog.at_level(logging.WARNING, logger="app.extraction"):
        kept = propose_structures(session, FakeLLM(batch))

    assert [t.name for t in kept] == ["offer-reward"]
    assert [t.name for t in latest_versions(session, TemplateKind.STRUCTURE)] == ["offer-reward"]
    # The type, not just the message: the catch is deliberately broad, so a malformed
    # proposal and a real bug inside `_to_structure` read identically without it.
    assert "_RejectedProposal" in caplog.text


def test_a_structure_cites_only_posts_the_model_was_shown(session):
    """Provenance has to be checkable — the filter `_to_template` has always applied.

    `held-out` is the case that matters and the one an "exists in the database" check
    would pass: the post is real, so only "was it in *this* sample" rejects it. That
    distinction is load-bearing once provenance becomes the approval gate, where citing
    posts the pattern was never derived from would approve a template covering nothing.
    """
    add_post(session, "shown", 185, "Body.")
    held_out = add_post(session, "held-out", 900, "Held out of every sample.")
    held_out.excluded_from_extraction = True
    session.add(held_out)
    session.flush()
    cited = {
        "structures": [
            {**OFFER_REWARD["structures"][0], "source_post_ids": ["shown", "held-out", "invented"]}
        ]
    }

    [structure] = propose_structures(session, FakeLLM(cited))

    assert structure.provenance == ["shown"]


def test_the_inspiration_cohort_reads_creator_posts_instead_of_montes(session):
    # A structure is a borrowable shape, so a creator's posts may teach one.
    theirs = add_post(session, "theirs", 1240, "Someone else's post body.")
    theirs.account_username = settings.inspiration_account
    session.add(theirs)
    session.flush()
    add_post(session, "ours", 185, "Monte's own post body.")

    # Citing "theirs" rather than the default "win-1", which this test never adds: a proposal
    # whose provenance filters to nothing is dropped, so the cohort assertions below would
    # pass over an empty list.
    llm = FakeLLM(structures_citing("theirs"))
    structures = propose_structures(session, llm, cohort=Cohort.INSPIRATION)

    assert "Someone else's post body." in llm.user
    assert "Monte's own post body." not in llm.user
    assert structures[0].body["cohort"] == "inspiration"


def test_a_structure_records_the_voice_cohort_by_default(session):
    add_post(session, "win-1", 185, "Body.")

    structures = propose_structures(session, FakeLLM(OFFER_REWARD))

    assert structures[0].body["cohort"] == "voice"


# --- coverage decides the status ----------------------------------------------------------


def _corpus(session, count: int) -> list[str]:
    ids = [f"post-{i}" for i in range(count)]
    for index, zid in enumerate(ids):
        add_post(session, zid, index, f"Post body number {index}.")
    return ids


def test_a_structure_covering_the_threshold_arrives_approved(session):
    """Structures had no status gate at all — every one took `create_template`'s PROPOSED.

    The same auto-approval hooks got, for the same reason: a human gate with a backlog
    protects nothing, and what it protected is bought by the append-only write.
    """
    ids = _corpus(session, APPROVE_AT_COVERAGE)

    (structure,) = propose_structures(session, FakeLLM(structures_citing(*ids)))

    assert len(structure.provenance) == APPROVE_AT_COVERAGE
    assert structure.status is TemplateStatus.APPROVED


def test_a_structure_one_short_of_the_threshold_arrives_proposed(session):
    """One below, not zero: a test at coverage 1 passes against `>= 1` too."""
    ids = _corpus(session, APPROVE_AT_COVERAGE)

    (structure,) = propose_structures(session, FakeLLM(structures_citing(*ids[:-1])))

    assert len(structure.provenance) == APPROVE_AT_COVERAGE - 1
    assert structure.status is TemplateStatus.PROPOSED


def test_coverage_is_counted_off_the_filtered_provenance_not_the_models_claim(session):
    """Invented ids must not buy approval, and they are measured rather than feared.

    All three VISUAL provenance citations in the database today join to no post row, and all
    15 structures cite nothing at all — the filter is what makes requiring the field safe.
    """
    ids = _corpus(session, APPROVE_AT_COVERAGE - 1)
    claimed = [*ids, "never-collected"]

    (structure,) = propose_structures(session, FakeLLM(structures_citing(*claimed)))

    assert len(claimed) == APPROVE_AT_COVERAGE
    assert structure.provenance == ids
    assert structure.status is TemplateStatus.PROPOSED


def test_a_structure_citing_nothing_it_was_shown_is_dropped(session):
    """`source_post_ids` is required by the 2.0.0 schema, so the code has to require it too.

    `traced_call` never validates a response against `output_schema`, so a structure with no
    citations is exactly what the 15 empty-provenance rows in the library today are: the
    schema asking for nothing and the code accepting it.
    """
    add_post(session, "win-1", 185, "Body.")
    uncited = {"structures": [{**OFFER_REWARD["structures"][0], "source_post_ids": []}]}

    assert propose_structures(session, FakeLLM(structures_citing("invented"))) == []
    assert propose_structures(session, FakeLLM(uncited)) == []
    assert latest_versions(session, TemplateKind.STRUCTURE) == []


def test_no_more_than_ten_structures_are_stored_and_the_rest_are_logged(session, caplog):
    """The prompt asks for at most 10; this is what makes that a fact rather than a hope."""
    (zid,) = _corpus(session, 1)
    batch = {
        "structures": [
            {**OFFER_REWARD["structures"][0], "name": f"s{i}", "source_post_ids": [zid]}
            for i in range(MAX_STRUCTURE_PROPOSALS + 3)
        ]
    }

    with caplog.at_level(logging.WARNING, logger="app.extraction"):
        kept = propose_structures(session, FakeLLM(batch))

    assert [t.name for t in kept] == [f"s{i}" for i in range(MAX_STRUCTURE_PROPOSALS)]
    assert len(latest_versions(session, TemplateKind.STRUCTURE)) == MAX_STRUCTURE_PROPOSALS
    # The whole message, not `"13" in caplog.text` — `caplog.text` carries `extraction.py:NNN`,
    # so a bare number can pass off the line number the day the warning moves.
    assert (
        f"model returned {MAX_STRUCTURE_PROPOSALS + 3} structures; "
        f"considered the first {MAX_STRUCTURE_PROPOSALS}" in caplog.text
    )


# --- reconciling into the families that already exist --------------------------------------

# The lines `_structure_prompt` writes for the two lists it offers, which are different lists
# and must not be confused: one names the hook families a structure may pair with, the other
# the structure families it may be reconciled into. A single `family_id: (\S+)` would match
# both, and the fake below would cite a *hook* id as its family — a reconcile test that fails
# for a reason looking nothing like its cause, and the same confusion a real model can make.
# The `\| ` anchor is what keeps them apart: `family_id` means "the family this proposal
# revises" in both prompts, and only the hooks carry a qualifier.
_OFFERED_HOOK = re.compile(r"\| hook family_id: (\S+)")
_OFFERED_FAMILY = re.compile(r"\| family_id: (\S+)")


class ReconcilingLLM(FakeLLM):
    """Cites whichever structure family the prompt offered, as a real model is asked to.

    Deliberately not a canned `family_id`: a fake that always cites an id the test chose would
    pass just as happily against a prompt that never listed the families, and listing them is
    half of what stops the family count growing.
    """

    def __init__(self, source_post_ids: list[str]):
        super().__init__({})
        self.source_post_ids = source_post_ids

    def complete_json(self, system: str, user: str, images=()) -> dict:
        super().complete_json(system, user, images)
        response = structures_citing(*self.source_post_ids)
        offered = _OFFERED_FAMILY.findall(user)
        if offered:
            response["structures"][0]["family_id"] = offered[0]
        return response


def structures_citing_family(family_id: str, *ids: str) -> dict:
    """`structures_citing`, with the proposal claiming to be a revision of an existing family."""
    response = structures_citing(*ids)
    response["structures"][0]["family_id"] = family_id
    return response


def existing_family(
    session,
    *,
    name: str = "offer-reward",
    provenance: list[str] | None = None,
    status: TemplateStatus = TemplateStatus.PROPOSED,
) -> Template:
    """A structure family a previous run left behind, at version 1."""
    return create_template(
        session,
        kind=TemplateKind.STRUCTURE,
        name=name,
        body={"sections": [{"name": "hook", "guidance": "Lead with the outcome."}]},
        provenance=provenance or [],
        status=status,
    )


def test_a_second_extraction_over_an_unchanged_corpus_adds_no_families(session):
    """15 structure families came out of one corpus because nothing ever looked one up."""
    ids = _corpus(session, 3)
    llm = ReconcilingLLM(ids)

    (first,) = propose_structures(session, llm)
    (second,) = propose_structures(session, llm)

    assert first.family_id == second.family_id
    assert (first.version, second.version) == (1, 2)
    assert len(latest_versions(session, TemplateKind.STRUCTURE)) == 1


def test_a_cited_family_gains_a_version_rather_than_a_sibling(session):
    ids = _corpus(session, 2)
    # A different name from the one the proposal carries, on purpose: the model's name wins,
    # which is the plain reading of "write version 2 from this proposal".
    family = existing_family(session, name="older-name", provenance=[ids[0]])

    (revised,) = propose_structures(
        session, FakeLLM(structures_citing_family(family.family_id, *ids))
    )

    assert (revised.family_id, revised.version) == (family.family_id, 2)
    assert revised.provenance == ids
    assert revised.name == "offer-reward"
    assert len(latest_versions(session, TemplateKind.STRUCTURE)) == 1
    # Append-only: version 1 still records what the earlier run actually saw.
    assert (family.name, family.provenance) == ("older-name", [ids[0]])


def test_the_prompt_offers_the_structure_families_that_already_have_names(session):
    ids = _corpus(session, 1)
    family = existing_family(session, name="deep-research", provenance=ids)

    llm = FakeLLM(structures_citing(*ids))
    propose_structures(session, llm)

    assert f"| family_id: {family.family_id}" in llm.user
    # The name as well as the id: an id alone is unreadable, and the model has to be able to
    # tell whether its structure is the same *shape* as this one.
    assert "deep-research" in llm.user


def test_an_unrecognised_family_id_mints_a_new_family(session):
    """A cited id naming nothing is a naming failure, not a reason to lose the structure."""
    ids = _corpus(session, 2)

    (structure,) = propose_structures(
        session, FakeLLM(structures_citing_family("no-such-family", *ids))
    )

    assert structure.family_id != "no-such-family"
    assert structure.version == 1
    assert structure.provenance == ids


def test_a_proposed_family_whose_coverage_has_crossed_the_floor_is_promoted(session):
    """`edit_template` copies the status of the row it revises (`templates.py:69`).

    Without an explicit promotion a family that landed at coverage 3 stays PROPOSED forever,
    even when a later run finds it covering forty posts.
    """
    ids = _corpus(session, APPROVE_AT_COVERAGE)
    family = existing_family(session, provenance=[ids[0]])

    (revised,) = propose_structures(
        session, FakeLLM(structures_citing_family(family.family_id, *ids))
    )

    assert revised.version == 2
    assert revised.status is TemplateStatus.APPROVED
    assert [t.family_id for t in usable_templates(session, TemplateKind.STRUCTURE)] == [
        family.family_id
    ]


def test_a_revision_one_short_of_the_floor_is_still_proposed(session):
    """One below, not zero: a test at coverage 1 passes against `>= 1` too."""
    ids = _corpus(session, APPROVE_AT_COVERAGE)
    family = existing_family(session, provenance=[ids[0]])

    (revised,) = propose_structures(
        session, FakeLLM(structures_citing_family(family.family_id, *ids[:-1]))
    )

    assert revised.status is TemplateStatus.PROPOSED
    assert usable_templates(session, TemplateKind.STRUCTURE) == []


def test_an_approved_family_whose_coverage_has_fallen_keeps_its_approval(session):
    """Nothing is ever auto-demoted. A run that finds a structure covering less than it did
    is evidence about that run, not grounds for taking a template out of the library."""
    ids = _corpus(session, APPROVE_AT_COVERAGE)
    family = existing_family(session, provenance=ids, status=TemplateStatus.APPROVED)

    (revised,) = propose_structures(
        session, FakeLLM(structures_citing_family(family.family_id, ids[0]))
    )

    assert revised.provenance == [ids[0]]
    assert revised.status is TemplateStatus.APPROVED


def test_a_second_proposal_citing_one_family_does_not_undo_the_first_ones_promotion(session):
    """Two proposals citing the same family, inside one batch, high coverage first.

    Against a snapshot taken before the loop the second proposal revises the *old* PROPOSED
    row, and `edit_template` copies that row's status — so version 3 lands PROPOSED on top of
    the APPROVED version 2 the first proposal just earned. That is an auto-demotion.
    """
    ids = _corpus(session, APPROVE_AT_COVERAGE)
    family = existing_family(session, provenance=[ids[0]])
    proposal = OFFER_REWARD["structures"][0]
    batch = {
        "structures": [
            {**proposal, "name": "broad", "family_id": family.family_id, "source_post_ids": ids},
            {
                **proposal,
                "name": "narrow",
                "family_id": family.family_id,
                "source_post_ids": ids[:1],
            },
        ]
    }

    kept = propose_structures(session, FakeLLM(batch))

    assert [t.version for t in kept] == [2, 3]
    (latest,) = latest_versions(session, TemplateKind.STRUCTURE)
    assert (latest.version, latest.status) == (3, TemplateStatus.APPROVED)


def test_a_proposal_citing_a_retired_family_is_dropped_and_mints_nothing(session, caplog):
    """A family a human retired is never silently revived — and never re-minted under a new id
    either, which is what a `create_template` fallback on the edit path would quietly do.

    The retired citation is *first* in the batch, the convention the hollow-sections test
    above sets.
    """
    ids = _corpus(session, APPROVE_AT_COVERAGE)
    family = existing_family(session, provenance=[ids[0]])
    retire(session, family)
    batch = structures_citing_family(family.family_id, *ids)
    batch["structures"].append(
        {**OFFER_REWARD["structures"][0], "name": "new", "source_post_ids": ids}
    )

    with caplog.at_level(logging.WARNING, logger="app.extraction"):
        kept = propose_structures(session, FakeLLM(batch))

    assert [t.name for t in kept] == ["new"]
    still = next(t for t in latest_versions(session, TemplateKind.STRUCTURE) if t.name != "new")
    assert (still.family_id, still.version) == (family.family_id, 1)
    assert still.status is TemplateStatus.RETIRED
    # Two families, not three: the retired one and the sibling that survived it.
    assert len(latest_versions(session, TemplateKind.STRUCTURE)) == 2
    assert "RetiredTemplateError" in caplog.text


def test_a_retired_family_is_not_offered_for_reconciliation(session):
    """It is in the lookup — that is what makes the drop above possible — and not in the
    prompt. `usable_templates` draws the same line: a withdrawn template is not offered."""
    ids = _corpus(session, 1)
    family = existing_family(session, name="withdrawn", provenance=ids)
    retire(session, family)

    llm = ReconcilingLLM(ids)
    propose_structures(session, llm)

    assert family.family_id not in llm.user
    assert "withdrawn" not in llm.user


def test_a_cited_family_whose_citations_all_fall_away_is_dropped_not_revised(session):
    """Empty filtered provenance is checked *before* the family is looked up.

    Reconciling first would write a version 2 that overwrites the family's real provenance
    with an empty one; dropping the proposal costs that family one version, which the next
    run recovers.
    """
    ids = _corpus(session, 2)
    family = existing_family(session, provenance=ids)

    kept = propose_structures(
        session, FakeLLM(structures_citing_family(family.family_id, "never-collected"))
    )

    assert kept == []
    (still,) = latest_versions(session, TemplateKind.STRUCTURE)
    assert (still.version, still.provenance) == (1, ids)


# --- a hook that has been renamed is still the hook it was ---------------------------------


def test_a_structure_pairs_with_a_renamed_hook_by_its_family_id(session):
    """The bug reconciliation introduced: a hook's name now changes on revision.

    `_to_template` writes `edit_template(name=...)` from the model's proposal, so version 2 of
    a family can carry a different name from version 1. A structure run that listed hook
    *names* and resolved `compatible_hooks` against them would silently pair with nothing the
    first time a hook was renamed — and `compatible_hook_families` stores ids, so the failure
    is invisible in the stored row: it is simply empty.

    The fake reads the id back out of the prompt rather than being handed it, which is what
    makes this cover the listing and the resolution together. Under name-based resolution the
    prompt offers `renamed`, the model cites the family id, and the lookup misses.
    """
    # `edit_template` carries the approved status across, so version 2 is the usable one.
    hook = a_hook(session, "original-name")
    edit_template(session, hook, name="renamed")
    (zid,) = _corpus(session, 1)

    class CitesTheOfferedHook(FakeLLM):
        def complete_json(self, system: str, user: str, images=()) -> dict:
            super().complete_json(system, user, images)
            return structures_citing(zid, hooks=tuple(_OFFERED_HOOK.findall(user)))

    (structure,) = propose_structures(session, CitesTheOfferedHook({}))

    assert structure.body["compatible_hook_families"] == [hook.family_id]
    assert [h.name for h in compatible_hooks(session, structure)] == ["renamed"]


def test_a_hook_cited_by_its_old_name_resolves_to_nothing(session):
    """The other half of the rename: names are no longer the key, so one buys nothing.

    Without this a model that kept citing names would half-work — the id path passing while
    every name citation silently produced an empty pairing.
    """
    hook = a_hook(session, "original-name")
    edit_template(session, hook, name="renamed")
    (zid,) = _corpus(session, 1)

    (structure,) = propose_structures(
        session, FakeLLM(structures_citing(zid, hooks=("original-name", "renamed")))
    )

    assert structure.body["compatible_hook_families"] == []


# --- the call extraction makes is recorded -------------------------------------------------


def test_the_trace_records_the_families_the_model_could_reconcile_into(session):
    """A structure that gained a version gained it because the prompt named its family.

    The same reason the hook trace records its own list, and the same identifier:
    `(family_id, version)`, never the id alone — the id does not say which version of the
    family the model was reading. The prompt version is pinned here as well as in
    `test_prompts.py`, because that file checks the constant and this checks what a call
    actually wrote.
    """
    ids = _corpus(session, 1)
    family = existing_family(session, provenance=ids)

    propose_structures(session, FakeLLM(structures_citing(*ids)))
    session.flush()

    (trace,) = session.exec(select(GenerationTrace)).all()
    assert trace.input_artifact_ids["families"] == f"{family.family_id}/1"
    assert (trace.prompt_name, trace.prompt_version) == ("extraction.structures", "2.0.0")


def test_an_uncovered_run_shows_the_model_only_the_posts_no_structure_describes(session):
    """The leftover loop, asked of structures.

    Deliberately a separate set from the hooks one: a post a hook speaks for may still have no
    structure describing it, so a shared "covered" set would silently shrink this sample by
    whatever hook extraction happened to have found.
    """
    ids = _corpus(session, 3)
    existing_family(session, provenance=[ids[0], ids[1]])

    llm = FakeLLM(structures_citing(ids[2]))
    propose_structures(session, llm, uncovered_only=True)

    # The prompt, not the return value: a flag that narrowed nothing still returns one
    # structure here, because the canned proposal cites a post that is in either sample.
    assert "Post body number 2." in llm.user
    assert "Post body number 0." not in llm.user
    assert "Post body number 1." not in llm.user


def test_a_corpus_every_structure_already_describes_returns_nothing_and_calls_no_model(session):
    """The loop terminating normally — no proposals, and never a raise."""
    ids = _corpus(session, 2)
    existing_family(session, provenance=ids)

    llm = FakeLLM(structures_citing(*ids))

    assert propose_structures(session, llm, uncovered_only=True) == []
    assert llm.user is None
