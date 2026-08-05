"""Does the finished post assert anything it cannot back up?

Two holes are what this file is about, and both were open before `app.verification` existed:

- `generation._research_findings` opens with `if dossier is None: return [], []`, so a
  `none`-mode draft — the mode whose whole definition is "no external factual claims" — was
  checked against nothing at all.
- In `light`/`deep` it compared the *plan* against the dossier, so a factual sentence the
  model invented while writing had never been a `PlannedClaim` and nothing looked at it.

The fakes are borrowed rather than restated: `QueuedLLM`, `Search`, `Renderer`,
`ImageRenderer`, `fetched` and `templates` from `tests/test_studio_workflow`, and the shared
description of what a passing workflow says from `tests/test_generation.WorkflowLLM.PLANNING`.
A second copy of either would drift, and a drifted copy of "what a passing workflow says" is
how a test ends up measuring a `failed` row.

Every candidate body here clears `gates.POST_MIN_CHARS` (80). A shorter one stops at
`failed_review` on the length gate before verification is reached, and the test then passes
for a reason that has nothing to do with evidence.
"""

import pytest
from fastapi.testclient import TestClient

from app import gates, verification
from app.db import get_session
from app.deps import get_llm
from app.generation import _research_findings, generate_reviewed_draft
from app.main import app
from app.models.research import DISPUTED, LIGHT, SUPPORTED
from app.models.stage import GenerationStage
from app.models.template import TemplateKind
from app.templates import approve, create_template
from tests.test_generation import add_post
from tests.test_studio_workflow import (
    ANGLE_FACT,
    ANGLE_NONE,
    BRIEF,
    ImageRenderer,
    QueuedLLM,
    Renderer,
    Search,
    fetched,
    templates,
)

# The dossier every factual case below is written against: one claim, cited to the one page
# `fetched` returns. Restated per test only where the citation is the thing under test.
CLAIM_TEXT = "Acme changed its checkout flow to remove one field"
SPAN = "Acme changed its checkout flow to remove one field"
OTHER_SPAN = "after reviewing support use"

SUPPORTED_DOSSIER = {
    "claims": [
        {
            "text": CLAIM_TEXT,
            "citations": [{"source": "S1", "span": SPAN, "stance": "supports"}],
        }
    ],
    "unknowns": [],
}
DISPUTED_DOSSIER = {
    "claims": [
        {
            "text": CLAIM_TEXT,
            "citations": [
                {"source": "S1", "span": SPAN, "stance": "supports"},
                {"source": "S1", "span": OTHER_SPAN, "stance": "contradicts"},
            ],
        }
    ],
    "unknowns": [],
}

# A run that searched, fetched, and could not settle anything worth citing. A real answer from
# `research.claims`, and the one that reads identically to `none` mode everywhere except in
# what actually happened.
EMPTY_DOSSIER: dict = {"claims": [], "unknowns": ["whether anything changed at all"]}

QUERIES = {"queries": ["Acme checkout field"]}
READY = {"deductions": []}


def factual(*sentences: str) -> dict:
    """A candidate whose body is the sentences given, joined as one paragraph."""
    return {
        "hook": "One checkout field was doing no useful work.",
        "body": " ".join(sentences),
        "visual_values": {},
    }


def asserted(text: str, *, kind: str = "factual", claim: str = "", idea_span: str = "") -> dict:
    return {"text": text, "kind": kind, "claim": claim, "idea_span": idea_span}


def run(session, llm, *, idea, requested_mode=None):
    hook, structure, visual = templates(session)
    return generate_reviewed_draft(
        session,
        llm,
        Search(),
        Renderer(),
        ImageRenderer(),
        idea=idea,
        hook_id=hook.id,
        structure_id=structure.id,
        visual_id=visual.id,
        requested_mode=requested_mode,
        research_fetcher=fetched,
    )


def factual_run(session, *, written, verification_answer, dossier=None, idea=None):
    """One `light`-mode workflow whose research settles `CLAIM_TEXT`."""
    llm = QueuedLLM(
        BRIEF,
        ANGLE_FACT,
        QUERIES,
        dossier or SUPPORTED_DOSSIER,
        written,
        verification_answer,
        READY,
    )
    draft = run(
        session,
        llm,
        idea=idea or "What changed in Acme checkout?",
        requested_mode=LIGHT,
    )
    return draft, llm


def names(draft, sentence: str) -> bool:
    """Whether some finding quotes this sentence of the post.

    The trailing full stop comes off first. What reaches the gate is the sentence as
    `gates._echoed_claims` splits it, and that split consumes the terminator — so a finding
    quotes "…within a month", never "…within a month.".
    """
    return any(sentence.rstrip(".") in item["detail"] for item in draft.gate_results)


# --- the evidence gate actually fires ---------------------------------------------------------


SUPPORTED_PARAPHRASE = factual(
    "Acme removed a field from its checkout flow.",
    "That is a useful prompt to look at the fields your own customers complete and ask "
    "which decision each one supports before you defend it.",
)


def test_a_supported_paraphrase_passes(session):
    """The false-positive direction, and the one that matters most.

    The post says in its own words what the dossier settled with a citation, so nothing here
    may stop it. A check that blocked this would be switched off within a week, and the
    project would be back to shipping uncited facts with a green suite.
    """
    draft, _ = factual_run(
        session,
        written=SUPPORTED_PARAPHRASE,
        verification_answer={
            "assertions": [asserted("Acme removed a field from its checkout flow.", claim="C1")]
        },
    )

    assert draft.generation_stage == GenerationStage.READY
    assert draft.gate_results == []
    assert draft.verification_result["assertions"][0]["verdict"] == SUPPORTED
    assert draft.verification_result["assertions"][0]["evidence"] == "C1"
    assert draft.verification_result["basis"] == "dossier"


def test_an_invented_statistic_blocks(session):
    """A number nobody researched. Never planned, so `_research_findings` never saw it."""
    invented = "Checkout completion rose forty two percent in the following quarter."
    draft, _ = factual_run(
        session,
        written=factual(
            "Acme removed a field from its checkout flow.",
            invented,
            "The lesson is to look at the fields your own customers complete before you "
            "defend any one of them.",
        ),
        verification_answer={
            "assertions": [
                asserted("Acme removed a field from its checkout flow.", claim="C1"),
                asserted(invented),
            ]
        },
    )

    assert draft.generation_stage == GenerationStage.FAILED_REVIEW
    assert any(item["gate"] == "uncited_claim" for item in draft.gate_results)
    assert names(draft, invented)


def test_an_invented_company_claim_blocks_even_when_the_verifier_quotes_a_clause(session):
    """The clause case, and it is the reason `_stated_sentence` exists.

    The invented claim sits inside a longer sentence, and the verifier quotes the clause
    rather than the whole line — which is what a model does however plainly the prompt asks
    for a sentence. `gates._echoed_claims` compares against the candidate's *sentences*, so
    the clause on its own reads about 0.64 alike to its own parent sentence, under the 0.72
    threshold, and the finding would never be raised. `verification._stated_sentence`
    resolves the clause back to the sentence carrying it, which scores 1.0.

    `test_the_clause_alone_would_not_have_fired_the_gate` below is the other half: it shows
    the finding disappears if the clause is fed to the gate unresolved.
    """
    sentence = (
        "Northwind Systems removed three checkout fields last spring, which is exactly why "
        "this pattern is worth naming out loud rather than quietly admiring from a distance."
    )
    draft, _ = factual_run(
        session,
        written=factual("Acme removed a field from its checkout flow.", sentence),
        verification_answer={
            "assertions": [
                asserted("Acme removed a field from its checkout flow.", claim="C1"),
                asserted("Northwind Systems removed three checkout fields last spring"),
            ]
        },
    )

    assert draft.generation_stage == GenerationStage.FAILED_REVIEW
    assert any(item["gate"] == "uncited_claim" for item in draft.gate_results)
    # The finding quotes past the end of the clause — `gates._echoed_claims` truncates a claim
    # at 80 characters, and "which is exactly" is the 60th onwards. So what reached the gate
    # was the whole sentence and not the fragment the verifier handed over.
    assert any("which is exactly" in item["detail"] for item in draft.gate_results)


def test_an_unplanned_unsupported_claim_blocks_where_the_plan_check_sees_nothing(session):
    """Hole two, stated as a test: the planned claim is settled and the post says more.

    The assertion below is deliberately *not* the planned claim, and the dossier supports the
    planned claim in full — so `_research_findings` returns two empty lists for these exact
    inputs, which the test asserts rather than implies. Every finding here therefore came
    from reading the finished words.
    """
    unplanned = "Two other retailers copied the same checkout change within a month."
    draft, _ = factual_run(
        session,
        written=factual(
            "Acme removed a field from its checkout flow.",
            unplanned,
            "Look at the fields your own customers complete before you defend any of them.",
        ),
        verification_answer={
            "assertions": [
                asserted("Acme removed a field from its checkout flow.", claim="C1"),
                asserted(unplanned),
            ]
        },
    )

    assert draft.generation_stage == GenerationStage.FAILED_REVIEW
    assert names(draft, unplanned)
    # The plan-side check, run over the same artifacts, has nothing to say about any of it.
    assert _research_findings([], None) == ([], [])


def test_a_disputed_claim_blocks_even_when_the_post_does_not_echo_its_wording(session):
    """The dossier disagrees with itself, and the post says it anyway in different words.

    `_research_findings` does put a `DISPUTED` claim into the contradicted list, but the gate
    matches by similarity: this post's sentence reads well under `CLAIM_ECHO_RATIO` alike to
    the dossier's own phrasing, so that route raises nothing. The finding has to name the
    *post's* sentence to prove it came from verification, which is what the last assertion
    checks — a finding quoting the dossier's wording would mean this test passed on the old
    path and measured nothing new.
    """
    restated = "That site now asks a shopper for one thing fewer than it used to."
    draft, _ = factual_run(
        session,
        dossier=DISPUTED_DOSSIER,
        written=factual(
            restated,
            "It is worth looking at which fields your own customers complete and which of "
            "them actually change a decision anybody makes.",
        ),
        verification_answer={"assertions": [asserted(restated, claim="C1")]},
    )

    assert draft.generation_stage == GenerationStage.FAILED_REVIEW
    assert any(item["gate"] == "contradicted_claim" for item in draft.gate_results)
    assert names(draft, restated)
    assert draft.verification_result["assertions"][0]["verdict"] == DISPUTED


def test_an_opinion_needs_no_citation_and_passes(session):
    """The other false-positive direction. An opinion is not an uncited fact.

    Blocking here costs a person an argument with the tool over a sentence that was never
    checkable in the first place — `gates.CLAIM_ECHO_RATIO` documents the same trade-off for
    the neighbouring check. `first_party` is in the same test because it is the same rule:
    the author is the source for their own work.
    """
    opinion = "Adding detail is usually the wrong answer to a decision that feels unclear."
    first_party = "We stopped writing that section altogether."
    llm = QueuedLLM(
        BRIEF,
        ANGLE_NONE,
        {
            "hook": "Clarity is often subtraction.",
            "body": f"{opinion} {first_party} The better move is to delete the sentence that "
            "changes no choice, then ask whether the next action is obvious.",
            "visual_values": {},
        },
        {
            "assertions": [
                asserted(opinion, kind="opinion"),
                asserted(first_party, kind="first_party"),
            ]
        },
        READY,
    )
    draft = run(session, llm, idea="why clearer product writing matters")

    assert draft.generation_stage == GenerationStage.READY
    assert draft.gate_results == []
    verdicts = {item["verdict"] for item in draft.verification_result["assertions"]}
    assert verdicts == {verification.NO_CITATION_NEEDED}
    assert not any(item["blocks"] for item in draft.verification_result["assertions"])


# --- `none` mode: the idea is the whole of what a post may assert ------------------------------

# Lower-cased, and the numbers are spelled out, because `research.recommend_mode` puts any
# digit — and any capitalised word that is not the first of a sentence — straight at the
# `light` floor. A `none`-mode test needs an idea the floor actually leaves at `none`, or it
# is a research test wearing the wrong name.
NONE_IDEA = (
    "we cut our own onboarding from fourteen days to three last spring, and the change that "
    "did it was deleting a form nobody read"
)
SUPPLIED_FACT = "We cut our onboarding from fourteen days to three."


def none_mode_run(session, *, sentence, verification_answer):
    llm = QueuedLLM(
        BRIEF,
        ANGLE_NONE,
        {
            "hook": "Clarity is often subtraction.",
            "body": f"{sentence} The lesson is to delete the step that changes no decision, "
            "then ask whether the next action is obvious to somebody new.",
            "visual_values": {},
        },
        verification_answer,
        READY,
    )
    return run(session, llm, idea=NONE_IDEA)


def test_a_fact_the_user_supplied_in_the_idea_passes_in_none_mode(session):
    """`none` mode is not "no facts". It is "no facts the operator did not supply"."""
    draft = none_mode_run(
        session,
        sentence=SUPPLIED_FACT,
        verification_answer={
            "assertions": [
                asserted(SUPPLIED_FACT, idea_span="onboarding from fourteen days to three")
            ]
        },
    )

    assert draft.generation_stage == GenerationStage.READY
    assert draft.gate_results == []
    assert draft.verification_result["basis"] == "idea"
    assert draft.verification_result["assertions"][0]["evidence"] == verification.IDEA_LABEL


def test_a_new_external_fact_blocks_in_none_mode(session):
    """The gate that did not exist. `none` mode had no evidence check at all before this.

    This is the mutation target named in the module docstring: delete the `none`-mode branch
    — which is the whole `if not findings:` verification block, since `none` mode reaches it
    with `dossier=None` — and this test is what fails.
    """
    invented = "Gartner reported the same pattern across the whole sector last year."
    draft = none_mode_run(
        session,
        sentence=invented,
        verification_answer={"assertions": [asserted(invented)]},
    )

    assert draft.generation_stage == GenerationStage.FAILED_REVIEW
    assert any(item["gate"] == "uncited_claim" for item in draft.gate_results)
    assert names(draft, invented)


def test_a_span_too_short_to_be_a_quotation_is_not_support(session):
    """`"" in anything` is True — the fabricated citation `QUOTE_MIN_CHARS` refuses.

    Without the minimum, every assertion in the post resolves as supported by the idea and
    `none` mode is back to checking nothing while reporting that it checked.
    """
    invented = "Gartner reported the same pattern across the whole sector last year."
    draft = none_mode_run(
        session,
        sentence=invented,
        verification_answer={"assertions": [asserted(invented, idea_span="the")]},
    )

    assert draft.generation_stage == GenerationStage.FAILED_REVIEW
    assert any(item["gate"] == "uncited_claim" for item in draft.gate_results)


# --- what may never count as evidence ----------------------------------------------------------


def test_a_voice_exemplar_is_never_factual_evidence(session):
    """A fact borrowed from an exemplar post is an uncited fact.

    Structural, because verification resolves only against dossier rows and the idea — but
    asserted behaviourally rather than by reading the prompt text, since a test that only
    checks a string is absent passes for the wrong reason after the next refactor. The
    exemplar carries a checkable number, the candidate asserts it, the verifier cites the
    exemplar's own words as its span, and it still blocks.

    The last two assertions are what stop this passing vacuously: the exemplar really did
    reach the writer, and really did not reach the verifier.
    """
    exemplar_fact = "our support volume fell by half in eleven weeks"
    add_post(session, "win-1", 900, f"Two years ago {exemplar_fact} and nobody wrote it down.")
    hook, structure, visual = templates(session)
    hook.provenance = ["win-1"]
    session.add(hook)
    session.flush()

    borrowed = "Support volume fell by half in eleven weeks."
    llm = QueuedLLM(
        BRIEF,
        ANGLE_NONE,
        {
            "hook": "Clarity is often subtraction.",
            "body": f"{borrowed} The lesson is to delete the step that changes no decision, "
            "then ask whether the next action is obvious to somebody new.",
            "visual_values": {},
        },
        {"assertions": [asserted(borrowed, idea_span=exemplar_fact)]},
        READY,
    )
    draft = generate_reviewed_draft(
        session,
        llm,
        Search(),
        Renderer(),
        ImageRenderer(),
        idea="why clearer product writing matters",
        hook_id=hook.id,
        structure_id=structure.id,
        visual_id=visual.id,
        research_fetcher=fetched,
    )

    assert draft.generation_stage == GenerationStage.FAILED_REVIEW
    assert any(item["gate"] == "uncited_claim" for item in draft.gate_results)
    assert exemplar_fact in llm.calls[2][1], "the exemplar never reached the writer"
    assert exemplar_fact not in llm.calls[3][1], "the exemplar reached the verifier"


def test_an_assertion_the_verifier_composed_rather_than_quoted_does_not_block(session):
    """The other false positive: a sentence the post does not contain.

    An extractor that paraphrases has not found something the post says, and refusing a draft
    over wording nobody wrote is exactly the failure that gets a check disabled. It is
    recorded — an extractor that mostly invents is worth being able to see — and it is not
    blocking.
    """
    draft, _ = factual_run(
        session,
        written=SUPPORTED_PARAPHRASE,
        verification_answer={
            "assertions": [asserted("Acme deleted an entire step from its purchase funnel.")]
        },
    )

    assert draft.generation_stage == GenerationStage.READY
    assert draft.gate_results == []
    recorded = draft.verification_result["assertions"][0]
    assert recorded["verdict"] == verification.NOT_IN_POST
    assert recorded["blocks"] is False


def test_a_claim_label_nobody_was_shown_resolves_to_nothing(session):
    """The fabricated citation, one layer up from `research._validated`.

    A model cannot invent a `C7` that resolves when only `C1` exists — and an assertion whose
    only evidence was that label is uncited, which blocks.
    """
    invented = "Two other retailers copied the same checkout change within a month."
    draft, _ = factual_run(
        session,
        written=factual(
            "Acme removed a field from its checkout flow.",
            invented,
            "Look at the fields your own customers complete before you defend any of them.",
        ),
        verification_answer={
            "assertions": [
                asserted("Acme removed a field from its checkout flow.", claim="C1"),
                asserted(invented, claim="C7"),
            ]
        },
    )

    assert draft.generation_stage == GenerationStage.FAILED_REVIEW
    assert names(draft, invented)


# --- the join to the gate ----------------------------------------------------------------------


CANDIDATE = {
    "hook": "One checkout field was doing no useful work.",
    "body": (
        "Northwind Systems removed three checkout fields last spring, which is exactly why "
        "this pattern is worth naming out loud rather than quietly admiring from a distance."
    ),
    "visual_values": {},
}


def gate_findings(session, claim: str) -> list[gates.Finding]:
    visual = create_template(
        session,
        kind=TemplateKind.VISUAL,
        name="textless-gate",
        body={"renderer": "html", "html": "<p>Pixii</p>"},
        slots=[],
    )
    approve(session, visual)
    return gates.check(
        CANDIDATE,
        template=visual,
        recent_posts=[],
        unsupported_claims=[claim],
    )


def test_a_resolved_sentence_fires_the_gate(session):
    """The join this whole stage rests on, tested directly rather than through a workflow.

    A workflow test that ends in `ready` looks identical whether verification passed the
    candidate or whether the gate silently failed to fire on what it was handed. This is the
    assertion that tells those two apart.
    """
    sentence = verification._stated_sentence(
        verification._full_text(CANDIDATE),
        "Northwind Systems removed three checkout fields last spring",
    )

    assert sentence is not None
    assert [f.gate for f in gate_findings(session, sentence)] == ["uncited_claim"]


def test_the_clause_alone_would_not_have_fired_the_gate(session):
    """Why `_stated_sentence` is not decoration, stated as its own assertion.

    The clause is genuinely in the post, and handing it to the gate unresolved raises
    nothing: against its own parent sentence it reads under `CLAIM_ECHO_RATIO`. This test is
    the deliberate mutation of the one above, kept rather than run once and thrown away —
    delete the sentence resolution and the invented-company test passes this way instead.
    """
    findings = gate_findings(session, "Northwind Systems removed three checkout fields last spring")

    assert findings == []


# --- unusable verifier output fails closed -----------------------------------------------------


def test_output_the_verifier_cannot_be_read_from_stops_the_draft(session):
    """A verifier nobody could read has not cleared anything.

    Fail closed, and visibly: `failed_review` with the reason, retryable from Studio. The
    alternative is a broken verifier switching the evidence gate off while every draft still
    arrives `ready`, which is the failure this stage exists to make impossible.
    """
    draft, _ = factual_run(
        session,
        written=SUPPORTED_PARAPHRASE,
        verification_answer={"assertion": "yes"},
    )

    assert draft.generation_stage == GenerationStage.FAILED_REVIEW
    assert "verifying:" in (draft.generation_error or "")
    assert draft.verification_result is None
    assert draft.readiness_result is None


def test_the_persisted_review_names_the_prompt_that_produced_it(session):
    draft, _ = factual_run(
        session,
        written=SUPPORTED_PARAPHRASE,
        verification_answer={
            "assertions": [asserted("Acme removed a field from its checkout flow.", claim="C1")]
        },
    )

    assert draft.verification_result["prompt_name"] == "verification.claims"
    assert draft.verification_result["prompt_version"] == "1.0.0"
    assert "1 assertion(s) checked against the research dossier" in (
        draft.verification_result["summary"]
    )


@pytest.mark.parametrize("path", ["/drafts/{id}", "/drafts"])
def test_the_review_reaches_the_api(session, path):
    """`DraftOut` is hand-mapped in two places, so a new column reaches neither by itself."""
    draft, _ = factual_run(
        session,
        written=SUPPORTED_PARAPHRASE,
        verification_answer={
            "assertions": [asserted("Acme removed a field from its checkout flow.", claim="C1")]
        },
    )
    session.flush()
    app.dependency_overrides[get_session] = lambda: session
    try:
        body = TestClient(app).get(path.format(id=draft.id)).json()
    finally:
        app.dependency_overrides.clear()

    found = body[0] if isinstance(body, list) else body
    assert found["verification_result"]["assertions"][0]["verdict"] == SUPPORTED


def test_regenerating_the_words_drops_the_review_of_the_old_ones(session):
    """A claim-to-source review describes sentences that no longer exist.

    `POST /drafts/{id}/regenerate-text` already clears `gate_results` and `readiness_result`
    for this reason and sends the draft back to `failed_review`. Leaving the review behind
    would show a reviewer the evidence behind wording nobody can read any more — and NULL is
    the right value rather than `{}`, because nothing has verified the new words at all.
    """
    draft, _ = factual_run(
        session,
        written=SUPPORTED_PARAPHRASE,
        verification_answer={
            "assertions": [asserted("Acme removed a field from its checkout flow.", claim="C1")]
        },
    )
    assert draft.verification_result is not None
    session.commit()

    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_llm] = lambda: QueuedLLM(
        {"hook": "Rewritten.", "body": "Something else entirely, and long enough to clear the "
         "length gate that would otherwise stop this before anything interesting happened."}
    )
    try:
        body = TestClient(app).post(f"/drafts/{draft.id}/regenerate-text").json()
    finally:
        app.dependency_overrides.clear()

    assert body["generation_stage"] == "failed_review"
    assert body["verification_result"] is None


def test_a_dossier_that_settled_nothing_is_not_reported_as_no_research(session):
    """`0` and NULL again, this time inside a prompt.

    A `light` run whose claim pass settled nothing hands verification the same empty claim
    list a `none`-mode run does. Telling the model "no research was run" about the first is
    a false statement about what happened — and it is invisible, because the verdict is
    `unsupported` either way, which is what the first assertion pins.
    """
    invented = "Two other retailers copied the same checkout change within a month."
    draft, llm = factual_run(
        session,
        dossier=EMPTY_DOSSIER,
        written=factual(
            invented,
            "Look at the fields your own customers complete before you defend any of them, "
            "because the answer is usually shorter than the argument about it.",
        ),
        verification_answer={"assertions": [asserted(invented)]},
    )

    assert draft.generation_stage == GenerationStage.FAILED_REVIEW
    assert names(draft, invented)
    verifier = next(
        user for system, user in llm.calls if system.startswith("You check a finished post")
    )
    assert "Research ran for this post and settled no claim" in verifier
    assert "No research was run" not in verifier
