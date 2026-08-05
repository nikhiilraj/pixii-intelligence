"""One research run, what it read, what it concluded, and what it could not settle.

Four tables, and the shape of them is an argument: a claim is a row, its support is a
*separate* row, and the link between them is a third. Storing citations as a list on the
claim would make "this claim has no citation" an empty array nobody queries — and the whole
point of this slice is that an unsupported claim is a state you can `WHERE` on, not an
absence a reader has to notice.

The page text itself is deliberately **not** stored. `Citation.span` keeps the handful of
words that support a claim and `ResearchSource.content_hash` keeps proof of what the bytes
were, which is what an auditor needs; a `text` column would reproduce someone else's page in
our database and put the whole page back into every prompt that reads it.
"""

from datetime import UTC, datetime

from sqlalchemy import Column, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

# How much external looking-up a brief gets. Blueprint §12.
#
# `none` — opinion, supplied source, creative-only; no external factual claims.
# `light` — confirm a small number of current facts against a few sources.
# `deep` — multi-query investigation with contradiction analysis.
NONE = "none"
LIGHT = "light"
DEEP = "deep"
MODES = (NONE, LIGHT, DEEP)

# A rank over the three modes, used for one comparison only: is the requested mode *below*
# the floor the brief requires. It is not an ordering anyone may sort or present by — see the
# "never rank" rule — and it exists because "the user may raise a mode, never lower it below
# the floor" is a sentence about depth and cannot be written without one.
DEPTH = {NONE: 0, LIGHT: 1, DEEP: 2}

# A job runs inside one call today, so it is running, it finished, or it died. The blueprint
# lists seven states (`queued`, `needs_review`, `cancelled`, `expired` …) and `daily_run`'s
# comment says why they are not here: they buy nothing while every transition happens inside
# one process inside one call. They arrive with the queue that makes a transition able to
# outlive the call that made it.
#
# **There is deliberately no `unsupported` or `no_evidence` job state.** A run that produced
# five uncited claims did its job: it found out that nothing supports them. That is
# `Claim.status` and `ResearchJob.open_questions`, and calling it a failed job would hide the
# most useful thing a dossier can say.
RUNNING = "running"
COMPLETED = "completed"
FAILED = "failed"
JOB_STATES = (RUNNING, COMPLETED, FAILED)

# What the evidence did to a claim.
#
# Derived from the citation rows, never taken from the model — a model asked for a status
# would happily answer `supported` for a claim it cited nothing for, which is precisely the
# failure this table is shaped to prevent.
SUPPORTED = "supported"  # at least one supporting citation, nothing against it
DISPUTED = "disputed"  # cited both ways: sources disagree, and that is a finding
REFUTED = "refuted"  # only contradicting citations
UNSUPPORTED = "unsupported"  # no citation survived validation. Not an error — an output.
CLAIM_STATUSES = (SUPPORTED, DISPUTED, REFUTED, UNSUPPORTED)

SUPPORTS = "supports"
CONTRADICTS = "contradicts"
STANCES = (SUPPORTS, CONTRADICTS)


class ResearchJob(SQLModel, table=True):
    """One question, the budget it was given, and what it actually spent.

    **`recommended_mode` and `mode` are both stored, always.** One column would make "the
    system asked for `light` and the run did `none`" unanswerable after the fact, and that
    silent downgrade is the exact failure blueprint §12 forbids. Two columns make the
    comparison a query rather than an inference.
    """

    __tablename__ = "research_job"

    id: int | None = Field(default=None, primary_key=True)

    question: str

    # Groups the model calls this run made, and is the same id `generation_trace` rows carry.
    # A dossier and the calls that produced it are one operation.
    correlation_id: str = Field(index=True)

    # What the floor detector said the brief needs, and what was actually run. The second is
    # never below the first; `research.resolve_mode` raises rather than clamping, so a row
    # where they differ always differs *upwards*.
    recommended_mode: str
    mode: str = Field(index=True)

    # Why the floor came out where it did — `["number", "organisation"]`. Kept because the
    # detector is a heuristic and the first question about a surprising floor is "what did it
    # see". `nullable=False` with a `[]` default, following `generation_trace.input_artifact_ids`:
    # an empty list is the honest answer for a brief that tripped no signal.
    mode_signals: list = Field(default_factory=list, sa_column=Column(JSONB, nullable=False))

    state: str = Field(default=RUNNING, index=True)

    # Nothing older than this many days counts as current, when the caller said so. NULL is
    # no policy expressed — not "any age is fine", which is a policy someone would have had
    # to state. Nothing enforces it yet: the fetcher cannot read a publication date off a
    # page (see `ResearchSource.published_at`), so a freshness rule has nothing to compare
    # against. Recorded because a dossier must say what policy it was run under, including
    # when the answer is "none was given".
    freshness_days: int | None = None

    # The ceilings this run was given, stored as run rather than read back from config: a
    # budget that changed next week must not rewrite what last week's run was allowed.
    max_queries: int
    max_fetches: int
    max_seconds: float

    # --- what it spent ----------------------------------------------------------------------
    #
    # **NULL is "that never happened"; `0` is a measurement.** `queries_run = 0` says a search
    # loop ran and issued no query; NULL says no search ran at all, which is what `none` mode
    # is. Same distinction `daily_run.drafts_created` documents, and the one a reader will
    # paper over first.
    queries_run: int | None = None
    # Distinct URLs the searches returned. NULL when no search ran; `0` when they came back
    # empty, which is a real and different fact — it means the queries found nothing.
    sources_found: int | None = None
    # Pages actually fetched and kept. NULL when no fetch loop ran; `0` when every candidate
    # was refused or failed, which is the case worth being able to see.
    sources_fetched: int | None = None
    # Billed completions, from the `SpendMeter` the run was given. `0` on a `none`-mode run is
    # correct and not a NULL: a meter existed and counted nothing, so the zero was observed.
    llm_calls: int | None = None

    # Which ceiling stopped the run early — `"queries"`, `"fetches"` or `"seconds"`. NULL when
    # none did. A string rather than a bool because "we stopped early" without saying which
    # limit bit sends the next person to read all three, and rather than a job state because
    # a run that hit its budget still completed and still produced a dossier.
    budget_exhausted: str | None = None

    # What the model said the evidence did not settle, as a list of sentences. NULL means the
    # claim pass never ran — a `none`-mode job, or one that died before it — while `[]` means
    # it ran and named nothing outstanding. The two are not the same answer.
    open_questions: list | None = Field(default=None, sa_column=Column(JSONB, nullable=True))

    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None

    error: str | None = None


class ResearchSource(SQLModel, table=True):
    """One page that was actually fetched during one job.

    A citation may only point at a row of this table belonging to its own job. That is the
    whole anti-fabrication story: a URL the model recited from memory has no row here, so
    nothing can cite it.
    """

    __tablename__ = "research_source"
    # One row per address per job. The same page reached twice in one run is one source, and
    # the constraint is what makes `sources_fetched` countable rather than an estimate.
    __table_args__ = (UniqueConstraint("job_id", "url", name="uq_research_source_job_url"),)

    id: int | None = Field(default=None, primary_key=True)
    job_id: int = Field(foreign_key="research_job.id", index=True)

    # **The address the bytes came from, after redirects** — `Fetched.url`, not the URL the
    # search provider handed us. A citation naming the requested address cites a page nobody
    # read, which is the specific bug `fetching.Fetched` documents at its own docstring.
    url: str

    # The address that was asked for, kept beside the one that answered. Same reason
    # `publication` keeps three forms of one instant: a reviewer checking "where did this come
    # from" needs the hop that happened, not only where it landed. Equal to `url` when there
    # was no redirect — not NULL, because a request was genuinely made to it.
    requested_url: str

    title: str | None = None

    # The host of the final URL, and nothing more. Named `publisher` because that is the
    # blueprint's field, but no masthead has ever been read: `fetching` strips attributes and
    # returns text, so `example.com` is the honest extent of what is known. It is also what
    # the independence of two sources is judged on, the day that judgement exists.
    publisher: str | None = None

    # NULL on every row this app writes, and structurally so: a publication date lives in
    # `<meta property="article:published_time">` or a JSON-LD block, and the fetcher's parser
    # never reads attributes — that is what strips `onclick=` and it is not being loosened for
    # a date. Upgrade path: a metadata pass over the raw markup, in the fetcher, behind the
    # same allowlist. Zero-filled or now()-filled would be configuration recorded as
    # observation, which is the mistake `generation_trace.model_deployment` names.
    published_at: datetime | None = None

    fetched_at: datetime

    # NULL on every row, deliberately. No provider assigns a trust tier and nothing here
    # computes one; a default of `"unknown"` would look like a measurement, and a default of
    # `"low"` would be an opinion the system never formed.
    trust_tier: str | None = None

    content_type: str
    # sha256 of the bytes as served. What lets a reviewer re-fetch the page and prove whether
    # they are reading what the model read.
    content_hash: str


class Claim(SQLModel, table=True):
    """One statement the run proposes, and what the evidence did to it.

    `status` is indexed because the query this table exists to serve is "show me every claim
    nothing supports". An unsupported claim is a first-class row with a first-class state, not
    a claim whose citation list happens to be empty.
    """

    __tablename__ = "claim"

    id: int | None = Field(default=None, primary_key=True)
    job_id: int = Field(foreign_key="research_job.id", index=True)

    text: str

    # Derived from this claim's citations, at write time, by `research._status`. Stored rather
    # than computed on read so that a query can filter on it and so that the status a reviewer
    # saw is the status recorded — the same reason `publication.draft_revision` is stored.
    status: str = Field(index=True)

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class Citation(SQLModel, table=True):
    """A claim, a source, and the words in that source which bear on it.

    Every row of this table was validated before it was written: the source is one this job
    fetched, and `span` is text that actually occurs in what the model was shown. A citation
    that failed either check is not written at all — the claim keeps its place and loses its
    support, which is `UNSUPPORTED`.
    """

    __tablename__ = "citation"

    id: int | None = Field(default=None, primary_key=True)
    claim_id: int = Field(foreign_key="claim.id", index=True)
    source_id: int = Field(foreign_key="research_source.id", index=True)

    # `supports` or `contradicts`. Contradiction is a citation, not a failure: a dossier whose
    # only recorded evidence was agreement would be a dossier that cannot disagree with
    # itself, and §12 asks for the opposite.
    stance: str = Field(index=True)

    # The short run of words holding the claim up. Bounded on both sides — see
    # `research.SPAN_MIN_CHARS` / `SPAN_MAX_CHARS`. Too short is not evidence (`""` is a
    # substring of every page, which is exactly the fabricated citation this check refuses);
    # too long reproduces the page, which is the copyright and prompt-bloat problem §12 names.
    span: str

    # The source's hash as it was when this span was taken. Denormalised from
    # `research_source` on purpose: the citation is the audit record, and it should still say
    # which bytes it was read out of if that row is ever re-fetched or corrected.
    source_content_hash: str

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
