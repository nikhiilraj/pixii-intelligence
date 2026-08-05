"""What the research routes are allowed to say, and what they may never say.

Three things are load-bearing here, and each is a way a correct dossier becomes a dishonest
screen on the way out of the database:

- **An unsupported claim must arrive as an unsupported claim.** `research.py` went to the
  trouble of making `UNSUPPORTED` a stored state rather than an empty citation list; a
  response that sends the claim and drops the status hands the client back the silence.
- **NULL and `0` are different answers and both survive the wire.** A `none`-mode run
  measured nothing and a `light` run that found nothing measured zero. Pydantic will fill a
  default in for either, silently, on the one response whose subject is what was measured.
- **A source is citable at its final URL.** The requested one is the address of a page nobody
  read, and it is right there in the same row waiting to be sent instead.

No search provider is configured and none is reached: every row below is written directly, the
way `test_research.py` writes them. These tests are about the seam, not about the run.
"""

import dataclasses
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app import research
from app.api_research import (
    CitationOut,
    ClaimOut,
    DossierOut,
    SourceOut,
    SpendOut,
    UnknownOut,
)
from app.db import get_session
from app.main import app
from app.models.research import (
    COMPLETED,
    CONTRADICTS,
    DISPUTED,
    LIGHT,
    NONE,
    REFUTED,
    SUPPORTED,
    SUPPORTS,
    UNSUPPORTED,
    Citation,
    Claim,
    ResearchJob,
    ResearchSource,
)


@pytest.fixture
def api(session) -> Iterator[TestClient]:
    app.dependency_overrides[get_session] = lambda: session
    yield TestClient(app)
    app.dependency_overrides.clear()


def job(session, **overrides) -> ResearchJob:
    """One research job, with the ceilings a `light` run is given.

    Spend left NULL unless a test sets it: NULL is what a row looks like before anything ran,
    and a helper that filled zeros in would make the distinction these tests are about
    unreachable from here.
    """
    row = ResearchJob(
        **{
            "question": "What did the EU AI Act change for model providers in 2026?",
            "correlation_id": "corr-1",
            "recommended_mode": LIGHT,
            "mode": LIGHT,
            "mode_signals": ["number", "law"],
            "state": COMPLETED,
            "max_queries": 2,
            "max_fetches": 4,
            "max_seconds": 45.0,
            "started_at": datetime(2026, 8, 5, 9, 30, tzinfo=UTC),
            **overrides,
        }
    )
    session.add(row)
    session.flush()
    return row


def source(session, job_row, url: str, requested: str | None = None, **overrides):
    row = ResearchSource(
        job_id=job_row.id,
        url=url,
        # Equal to `url` when nothing redirected — not NULL, because a request was genuinely
        # made to it.
        requested_url=requested or url,
        title="Article",
        publisher="example.com",
        fetched_at=datetime(2026, 8, 5, 9, 31, tzinfo=UTC),
        content_type="text/html",
        content_hash="a" * 64,
        **overrides,
    )
    session.add(row)
    session.flush()
    return row


def claim(session, job_row, text: str, status: str) -> Claim:
    row = Claim(job_id=job_row.id, text=text, status=status)
    session.add(row)
    session.flush()
    return row


def cite(session, claim_row, source_row, stance: str = SUPPORTS) -> Citation:
    row = Citation(
        claim_id=claim_row.id,
        source_id=source_row.id,
        stance=stance,
        span="providers of general purpose models must publish training summaries",
        source_content_hash=source_row.content_hash,
    )
    session.add(row)
    session.flush()
    return row


# --- the route exists, and says so about a job that does not -----------------------------------


def test_a_real_job_answers_with_its_dossier(api, session):
    """The 200 half of the 404 test below, in the same file on purpose.

    `CLAUDE.md` names the audit that found six assertions checking for a `404` which passed
    against routes that did not exist, because the framework 404s any unrouted path. A 404
    assertion is only evidence when something in the same file proves the route is mounted.
    """
    row = job(session)

    response = api.get(f"/research/{row.id}")

    assert response.status_code == 200
    assert response.json()["job_id"] == row.id


def test_a_missing_job_is_a_404_with_the_reason(api):
    """And the `detail` is asserted, not just the status.

    An unrouted path 404s with `{"detail": "Not Found"}`. Reading the module's own sentence is
    what tells this test apart from one passing against nothing at all.
    """
    response = api.get("/research/999999")

    assert response.status_code == 404
    assert response.json()["detail"] == "no research job 999999"


# --- an uncited claim is visibly uncited --------------------------------------------------------


def test_an_unsupported_claim_arrives_as_unsupported(api, session):
    """The single most important thing on this seam.

    Not "the claim is present" — an unsupported claim being present with its status dropped is
    exactly the failure `research.py` restructured a table to prevent.
    """
    row = job(session)
    claim(session, row, "The Act fines providers 7% of turnover.", UNSUPPORTED)

    body = api.get(f"/research/{row.id}").json()

    assert [c["status"] for c in body["claims"]] == [UNSUPPORTED]


def test_an_unsupported_claim_is_also_listed_as_an_unknown(api, session):
    """Both places, and the overlap is deliberate.

    A reader asking "what does this dossier not know" must not have to reconstruct the answer
    from a status field on a list they may not have read to the end of.
    """
    row = job(session)
    uncited = claim(session, row, "The Act fines providers 7% of turnover.", UNSUPPORTED)

    body = api.get(f"/research/{row.id}").json()

    assert {"text": "The Act fines providers 7% of turnover.", "claim_id": uncited.id} in body[
        "unknowns"
    ]


def test_a_supported_claim_is_not_listed_as_an_unknown(api, session):
    """The other direction, without which "put every claim in unknowns" would pass above."""
    row = job(session)
    src = source(session, row, "https://example.com/a")
    supported = claim(session, row, "The Act took effect in 2026.", SUPPORTED)
    cite(session, supported, src)

    body = api.get(f"/research/{row.id}").json()

    assert body["unknowns"] == []
    assert body["claims"][0]["supporting_citation_ids"] != []


def test_a_refuted_claim_is_not_an_uncited_one(api, session):
    """Why `status` is sent rather than derived from the id lists on the client.

    `supporting_citation_ids` is empty for an `unsupported` claim and empty for a `refuted`
    one. A client inferring "uncited" from the empty list would call a claim two sources
    actively contradict an uncited claim, which is the opposite of what happened to it.
    """
    row = job(session)
    src = source(session, row, "https://example.com/a")
    refuted = claim(session, row, "The Act exempts open-source models entirely.", REFUTED)
    against = cite(session, refuted, src, stance=CONTRADICTS)

    body = api.get(f"/research/{row.id}").json()
    sent = body["claims"][0]

    assert sent["status"] == REFUTED
    assert sent["supporting_citation_ids"] == []
    assert sent["contradicting_citation_ids"] == [against.id]


def test_an_open_question_is_an_unknown_with_no_claim(api, session):
    """The second kind of unknown: something the model raised and made no claim about.

    `claim_id` NULL is what says there is no row to go and look at, and it is the field a
    client needs in order not to offer a link to one.
    """
    row = job(session, open_questions=["Whether the transition period applies to fine-tunes."])

    body = api.get(f"/research/{row.id}").json()

    assert body["unknowns"] == [
        {"text": "Whether the transition period applies to fine-tunes.", "claim_id": None}
    ]


# --- contradictions are a finding, and they are on the wire ------------------------------------


def test_contradictions_are_sent_as_their_own_list(api, session):
    """Disputed and refuted claims, where a reader will look for them.

    Not an error state and not filtered out: a dossier that cannot disagree with itself is the
    thing §12 asks for the opposite of.
    """
    row = job(session)
    src = source(session, row, "https://example.com/a")
    disputed = claim(session, row, "Enforcement begins in August.", DISPUTED)
    cite(session, disputed, src, stance=SUPPORTS)
    cite(session, disputed, src, stance=CONTRADICTS)
    claim(session, row, "The Act was passed.", SUPPORTED)

    body = api.get(f"/research/{row.id}").json()

    assert [c["id"] for c in body["contradictions"]] == [disputed.id]
    # And the claim is still in `claims`. Moving it out would make the two lists disjoint and
    # a client rendering only `claims` would drop the most interesting row in the dossier.
    assert disputed.id in [c["id"] for c in body["claims"]]


# --- a source is what was read, not what was asked for -----------------------------------------


def test_a_source_carries_the_url_that_actually_served_the_bytes(api, session):
    """The citable address is the final one, and the requested one is sent beside it.

    Both, because a reviewer checking a source needs the hop that happened — and because a
    response carrying only one of them invites whoever renders it to link the wrong one.
    """
    row = job(session)
    source(
        session,
        row,
        "https://example.com/final-article",
        requested="https://short.link/xyz",
    )

    sent = api.get(f"/research/{row.id}").json()["sources"][0]

    assert sent["url"] == "https://example.com/final-article"
    assert sent["requested_url"] == "https://short.link/xyz"


def test_a_source_is_never_presented_as_verified(api, session):
    """Fetched means read, not trusted.

    `trust_tier` and `published_at` are NULL on every row this application writes — nothing
    assigns a tier and the fetcher's parser never reads the attributes a date lives in — and
    they stay NULL on the wire. A `"unknown"` or a `now()` here would be configuration
    arriving as observation, and the screen would have no way to tell.
    """
    row = job(session)
    source(session, row, "https://example.com/a")

    sent = api.get(f"/research/{row.id}").json()["sources"][0]

    assert sent["trust_tier"] is None
    assert sent["published_at"] is None
    # What *is* known, and what makes the fetch checkable: the bytes' hash and when they were
    # read. Absent these, "we fetched it" is unfalsifiable.
    assert sent["content_hash"] == "a" * 64
    assert sent["fetched_at"] is not None


# --- NULL is not zero ---------------------------------------------------------------------------


def test_spend_is_null_where_nothing_ever_ran(api, session):
    """A `none`-mode run reaches no loop at all, and its dossier must not report zeros.

    `0` presents an absence as a measurement, which is the rule this project prints `—` for.
    Pydantic would fill these in without complaint if the response model gave them defaults —
    which is why the model declares every one as `int | None`.
    """
    row = job(session, mode=NONE, recommended_mode=NONE, mode_signals=[])

    spend = api.get(f"/research/{row.id}").json()["spend"]

    assert spend == {
        "queries": None,
        "sources_found": None,
        "sources_fetched": None,
        "llm_calls": None,
        "budget_exhausted": None,
    }


def test_a_measured_zero_survives_as_zero(api, session):
    """The other half, and the reason the rule is not "print `—` for falsy".

    `sources_found = 0` says the queries ran and came back empty, which is a real and
    different fact from "no query was ever issued". A response collapsing it to NULL, or a
    client collapsing it to `—`, would lose the finding.
    """
    row = job(session, queries_run=2, sources_found=0, sources_fetched=0, llm_calls=1)

    spend = api.get(f"/research/{row.id}").json()["spend"]

    assert spend["queries"] == 2
    assert spend["sources_found"] == 0
    assert spend["sources_fetched"] == 0
    assert spend["llm_calls"] == 1


def test_the_ceiling_that_stopped_the_run_is_reported(api, session):
    """"Two sources because the fetch ceiling bit" and "two sources is all there were" are the
    same two numbers and a different fact. Without this field a sparse dossier is unreadable."""
    row = job(
        session, queries_run=2, sources_found=9, sources_fetched=4, budget_exhausted="fetches"
    )

    assert api.get(f"/research/{row.id}").json()["spend"]["budget_exhausted"] == "fetches"


def test_the_mode_the_floor_asked_for_is_sent_beside_the_one_that_ran(api, session):
    """One field would make a silent downgrade unanswerable after the fact — the exact failure
    `ResearchJob` stores two columns to prevent. `mode_signals` says what the detector saw."""
    body = api.get(f"/research/{job(session).id}").json()

    assert body["mode"] == LIGHT
    assert body["recommended_mode"] == LIGHT
    assert body["mode_signals"] == ["number", "law"]


# --- order is chronology, never ranking ---------------------------------------------------------


def test_claims_keep_their_own_order(api, session):
    """Insertion order, with the unsupported claim in the middle where it was written.

    Sorting the uncited ones to the top is one line and is the tempting move — `dossier()`'s
    docstring refuses it, and this is the assertion that would fail if someone added it here.
    Prominence on the screen is a badge and a summary, not a reordering.
    """
    row = job(session)
    src = source(session, row, "https://example.com/a")
    first = claim(session, row, "One.", SUPPORTED)
    cite(session, first, src)
    second = claim(session, row, "Two.", UNSUPPORTED)
    third = claim(session, row, "Three.", SUPPORTED)
    cite(session, third, src)

    body = api.get(f"/research/{row.id}").json()

    assert [c["id"] for c in body["claims"]] == [first.id, second.id, third.id]


def test_the_index_lists_runs_newest_first(api, session):
    """A chronology — the ordering `GET /drafts` uses, and the only one over these rows that
    carries no claim about their quality."""
    older = job(session, started_at=datetime(2026, 8, 1, 9, 0, tzinfo=UTC))
    newer = job(session, started_at=datetime(2026, 8, 5, 9, 0, tzinfo=UTC))

    body = api.get("/research").json()

    assert [row["job_id"] for row in body] == [newer.id, older.id]
    # And in the same spelling the dossier uses. Both columns are `timestamp without time
    # zone`, so one route normalising and the other not would put `…T09:00:00Z` on one screen
    # and `…T09:00:00` on the next, for the same instant.
    assert body[0]["researched_at"] == api.get(f"/research/{newer.id}").json()["researched_at"]


def test_the_index_reports_no_counts(api, session):
    """No claim count and no source count beside a question.

    Two counts side by side read as a comparison, and there is nothing here to compare — the
    same reasoning as the "never rank" rule, one layer out. Recognising a run needs the
    question and the date; it does not need a number that looks like a score.
    """
    row = job(session)
    source(session, row, "https://example.com/a")
    claim(session, row, "One.", UNSUPPORTED)

    sent = api.get("/research").json()[0]

    assert set(sent) == {
        "job_id",
        "question",
        "mode",
        "recommended_mode",
        "state",
        "researched_at",
    }


# --- the hand-mapping trap ----------------------------------------------------------------------

# Which dataclass in `research.py` each response model is the wire form of. The parity test
# below walks these rather than listing field names, so a field added to the dataclass and
# forgotten here fails a test instead of being invisible.
_MAPPED: tuple[tuple[type, type[BaseModel], set[str]], ...] = (
    (research.ResearchDossier, DossierOut, set()),
    (research.DossierSource, SourceOut, set()),
    (research.DossierClaim, ClaimOut, set()),
    (research.DossierCitation, CitationOut, set()),
    (research.Unknown, UnknownOut, set()),
    (research.Spend, SpendOut, set()),
)


@pytest.mark.parametrize("dataclass_type, model, allowed_to_omit", _MAPPED)
def test_every_dossier_field_reaches_the_api(dataclass_type, model, allowed_to_omit):
    """The trap `DraftOut` documents, caught by a test rather than by remembering.

    Every field is added in two places — the response model and the `_out` mapping — and
    nothing derives one from the other, so a new field on `ResearchDossier` arrives with a
    green `research.py`, a green `test_research.py` and a frontend that never sees it. This
    walks the dataclass, so it fails for a field nobody thought to add here; the third element
    of `_MAPPED` is where a deliberate omission is written down and explained, and it is empty
    today because nothing is deliberately omitted.
    """
    declared = {field.name for field in dataclasses.fields(dataclass_type)}
    missing = declared - set(model.model_fields) - allowed_to_omit

    assert not missing, (
        f"{dataclass_type.__name__}.{sorted(missing)} is not on {model.__name__}. "
        "Add it there and in the matching `_out`, or record why not in `_MAPPED`."
    )


def test_the_mapping_fills_every_field_it_declares(api, session):
    """The other half of the trap: a field on the model that `_out` never fills.

    Pydantic would refuse a *missing* required field, but the failure this catches is the one
    it would not — a field declared optional and quietly left at its default while the
    dataclass has a real value for it. Every field of a fully-populated dossier is asserted
    non-null in one place, so nothing can be declared and forgotten.
    """
    row = job(
        session,
        freshness_days=30,
        queries_run=2,
        sources_found=3,
        sources_fetched=1,
        llm_calls=2,
        budget_exhausted="fetches",
        open_questions=["Whether fine-tunes count."],
    )
    src = source(session, row, "https://example.com/final", requested="https://short.link/x")
    disputed = claim(session, row, "Enforcement begins in August.", DISPUTED)
    cite(session, disputed, src, stance=SUPPORTS)
    cite(session, disputed, src, stance=CONTRADICTS)

    body = api.get(f"/research/{row.id}").json()

    # The two structurally-NULL source fields are the only nulls a populated dossier may have,
    # and they have their own test above saying why they are null rather than unfilled.
    unset = [key for key, value in body.items() if value in (None, [], {})]
    assert unset == []
    unset_source = [
        key
        for key, value in body["sources"][0].items()
        if value is None and key not in {"trust_tier", "published_at"}
    ]
    assert unset_source == []
    assert [key for key, value in body["spend"].items() if value is None] == []
    assert [key for key, value in body["citations"][0].items() if value is None] == []
