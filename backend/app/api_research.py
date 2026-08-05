"""A research dossier, on the wire.

Everything here reads through `research.dossier()` and nothing reads the tables behind it.
That is not tidiness: `dossier()` is where "an unsupported claim is a listed unknown", "a
source's citable address is the one after redirects" and "NULL is not zero" are decided, and a
second assembly of the same shape here would be a second place for those to be decided
differently. The one exception is the index below, which selects `research_job` directly — it
is a list of jobs, not a dossier, and building a dossier per row to render five columns would
read every source and claim in the database to answer "what runs exist".

**Hand-mapped, and every field has to be added in two places** — the response models here and
the `_out` functions below. `DraftOut` explains why at length and this module inherits the
problem exactly: a new field on `ResearchDossier` arrives with a green `research.py`, a green
`test_research.py` and a frontend that never sees it. `test_every_dossier_field_reaches_the_api`
is what keeps the next one from being missed, and it walks the dataclass rather than listing
names, so it fails for a field nobody thought to add here.
"""

from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlmodel import col, desc, select

from app import research
from app.db import utc
from app.deps import SessionDep
from app.models.research import ResearchJob

router = APIRouter(prefix="/research", tags=["research"])


class SourceOut(BaseModel):
    """One page that was actually fetched, and everything known about how much to trust it.

    Which is: very little, and the fields say so rather than filling in. `trust_tier` and
    `published_at` are NULL on every row this application writes — no provider assigns a tier
    and the fetcher's parser never reads the attributes a publication date lives in — and
    `publisher` is the hostname of the final URL and no masthead. They are on the wire anyway,
    as nulls, because a screen that cannot say "nobody assessed this" is a screen that lets
    "we fetched it" read as "we vetted it".
    """

    id: int

    # **The address the bytes came from, after redirects.** The pair is sent, not just this
    # one: a citation naming the requested address cites a page nobody read, and a reader
    # checking a source needs to see the hop that happened rather than only where it landed.
    url: str
    requested_url: str

    title: str | None
    publisher: str | None
    published_at: datetime | None
    fetched_at: datetime
    trust_tier: str | None

    # sha256 of the bytes as served. What lets a reviewer re-fetch and prove whether they are
    # reading what the model read — which is the only sense in which any of this is verified.
    content_hash: str


class CitationOut(BaseModel):
    """A claim, a source, and the words in that source which bear on it."""

    id: int
    claim_id: int
    source_id: int
    # `supports` or `contradicts`. A contradiction is a citation, not a failure.
    stance: str
    span: str
    # The source's hash as it was when this span was taken, which can differ from the source
    # row's own if that page were ever re-fetched. Denormalised in the table for that reason
    # and carried here for the same one.
    source_content_hash: str


class ClaimOut(BaseModel):
    """One statement, and what the evidence did to it.

    `status` is on the wire as its own field and is never inferred from the two id lists.
    `supporting_citation_ids == []` is true for an `unsupported` claim and also for a
    `refuted` one, and a client deriving "uncited" from an empty list would call a claim two
    sources actively contradict an uncited one.
    """

    id: int
    text: str
    # `supported` / `disputed` / `refuted` / `unsupported` — `CLAIM_STATUSES`.
    status: str
    supporting_citation_ids: list[int]
    contradicting_citation_ids: list[int]


class UnknownOut(BaseModel):
    """Something the run could not settle.

    `claim_id` is the id of the claim nothing supported, or NULL for a question the model
    raised and made no claim about. Both are unknowns; only one has a row to go and look at.
    """

    text: str
    claim_id: int | None


class SpendOut(BaseModel):
    """What the run cost. **Every field is `int | None` and the NULL is the point.**

    NULL is "that step never ran"; `0` is a measurement. `queries = 0` is a search loop that
    issued nothing, and NULL is a `none`-mode run where there was no loop at all. A default of
    `0` here would be Pydantic quietly answering a question nobody measured, on the one
    response whose job is to say what was and was not measured.
    """

    queries: int | None
    sources_found: int | None
    sources_fetched: int | None
    llm_calls: int | None
    # Which ceiling stopped the run early — `"queries"`, `"fetches"`, `"seconds"` — or NULL
    # when none did. Sent because "two sources because the fetch ceiling bit" and "two sources
    # is all there were" are the same two numbers and a different fact.
    budget_exhausted: str | None


class DossierOut(BaseModel):
    """One run: what it asked, what it read, what it concluded, and what it could not settle.

    The four lists are sent whole and in `dossier()`'s own order, which is insertion order.
    **Nothing here is sorted by status or by support**, and the temptation is real — putting
    the unsupported claims first is one line. `dossier()`'s docstring refuses it for the
    reason the project's "never rank" rule gives: a list in an order implies the order means
    something, and this dossier has ~zero runs behind it to mean anything with.

    `unknowns` and `contradictions` overlap `claims` on purpose rather than by accident.
    Every unsupported claim appears in both, and a reader asking "what does this not know"
    must not have to reconstruct that list from a status field they might not have read.
    """

    job_id: int
    question: str

    # What was run, and what the floor detector said was needed. Both, always: one field would
    # make "the system asked for `light` and the run did `none`" unanswerable afterwards, and
    # `mode_signals` is what the detector saw, because the first question about a surprising
    # floor is what tripped it.
    mode: str
    recommended_mode: str
    mode_signals: list[str]

    # `running` / `completed` / `failed`. **`completed` with five unsupported claims is a
    # success**, and there is no job state that means "found nothing" — see `models/research.py`.
    # A failed run's `error` is not on this seam: `ResearchDossier` does not carry it.
    state: str

    researched_at: datetime
    # The staleness policy this run was given, in days, or NULL for none stated. Nothing
    # enforces it — the fetcher cannot read a publication date — and it is reported because a
    # dossier must say what policy it ran under, including when the answer is "none".
    freshness_days: int | None

    sources: list[SourceOut]
    claims: list[ClaimOut]
    citations: list[CitationOut]
    unknowns: list[UnknownOut]
    contradictions: list[ClaimOut]
    spend: SpendOut


class ResearchJobOut(BaseModel):
    """One row of the index: enough to recognise a run by, and nothing it concluded.

    No claim counts and no source counts. Counting the rows of every job to draw a list is the
    whole database read to answer "what runs exist", and a count beside a question reads as a
    score for it — `2 sources` next to `5 sources` invites exactly the comparison the corpus
    cannot support.
    """

    job_id: int
    question: str
    mode: str
    recommended_mode: str
    state: str
    researched_at: datetime


def _source_out(source: research.DossierSource) -> SourceOut:
    return SourceOut(
        id=source.id,
        url=source.url,
        requested_url=source.requested_url,
        title=source.title,
        publisher=source.publisher,
        published_at=source.published_at,
        fetched_at=source.fetched_at,
        trust_tier=source.trust_tier,
        content_hash=source.content_hash,
    )


def _claim_out(claim: research.DossierClaim) -> ClaimOut:
    return ClaimOut(
        id=claim.id,
        text=claim.text,
        status=claim.status,
        supporting_citation_ids=list(claim.supporting_citation_ids),
        contradicting_citation_ids=list(claim.contradicting_citation_ids),
    )


def _citation_out(citation: research.DossierCitation) -> CitationOut:
    return CitationOut(
        id=citation.id,
        claim_id=citation.claim_id,
        source_id=citation.source_id,
        stance=citation.stance,
        span=citation.span,
        source_content_hash=citation.source_content_hash,
    )


def _out(found: research.ResearchDossier) -> DossierOut:
    return DossierOut(
        job_id=found.job_id,
        question=found.question,
        mode=found.mode,
        recommended_mode=found.recommended_mode,
        mode_signals=list(found.mode_signals),
        state=found.state,
        researched_at=found.researched_at,
        freshness_days=found.freshness_days,
        sources=[_source_out(s) for s in found.sources],
        claims=[_claim_out(c) for c in found.claims],
        citations=[_citation_out(c) for c in found.citations],
        unknowns=[UnknownOut(text=u.text, claim_id=u.claim_id) for u in found.unknowns],
        contradictions=[_claim_out(c) for c in found.contradictions],
        spend=SpendOut(
            queries=found.spend.queries,
            sources_found=found.spend.sources_found,
            sources_fetched=found.spend.sources_fetched,
            llm_calls=found.spend.llm_calls,
            budget_exhausted=found.spend.budget_exhausted,
        ),
    )


@router.get("")
def list_research(session: SessionDep, limit: int = 100) -> list[ResearchJobOut]:
    """Every research run, newest first.

    Newest first is a chronology and not a ranking — the same ordering `GET /drafts` uses, and
    the only ordering over these rows that carries no claim about their quality.

    ponytail: this route exists because nothing links a draft to the research behind it.
    `Draft` has no `research_job_id` column, so a human on the draft screen has no other way to
    reach a dossier. Upgrade path: when a draft records its job, the screen resolves the id
    from the draft and this becomes an index nobody has to use.
    """
    rows = session.exec(
        select(ResearchJob).order_by(desc(col(ResearchJob.started_at))).limit(limit)
    ).all()
    return [
        ResearchJobOut(
            job_id=row.id or 0,
            question=row.question,
            mode=row.mode,
            recommended_mode=row.recommended_mode,
            state=row.state,
            # Through the dossier's own field name, and through `db.utc` for the same reason
            # `dossier()` does: this column is `timestamp without time zone`, so the value
            # comes back naive, and one of these two routes serialising `…T10:05:00` while
            # the other serialises `…T10:05:00Z` would put two spellings of one instant in
            # front of the same screen. `started_at` is when the run began, which is what
            # "researched at" means for a job that finishes inside one call.
            researched_at=utc(row.started_at),
        )
        for row in rows
    ]


@router.get("/{job_id}")
def get_research(session: SessionDep, job_id: int) -> DossierOut:
    """One dossier, rebuilt from the rows.

    404 for a job that does not exist, with the module's own sentence. `dossier()` raises
    `LookupError` and translating it here is the whole of this route's error handling: there is
    no partial dossier and no empty one — a job either has rows or does not exist.
    """
    try:
        found = research.dossier(session, job_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _out(found)
