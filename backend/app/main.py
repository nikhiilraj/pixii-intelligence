from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, time

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlmodel import col, select

from app.api_assets import router as assets_router
from app.api_drafts import router as drafts_router
from app.api_templates import router as templates_router
from app.config import settings
from app.corpus import (
    ManualPostRejected,
    add_manual_post,
    ingest_history,
    ingest_posts,
    upsert_linkedin_posts,
)
from app.db import engine
from app.deps import SessionDep
from app.metrics import sync_metrics, template_performance
from app.models.draft import Draft
from app.models.metric import MetricSnapshot
from app.models.post import Post, PostSource, Verdict
from app.models.template import TemplateStatus
from app.scheduler import shutdown as stop_scheduler
from app.scheduler import start as start_scheduler
from app.templates import latest_versions
from app.zernio import ZernioClient, ZernioResponseError


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Scheduled jobs live for the lifetime of the process."""
    start_scheduler()
    yield
    stop_scheduler()


app = FastAPI(title="Pixii Intelligence", version="0.1.0", lifespan=lifespan)

# Locally cached post media, served so the dashboard can display it without reaching
# back out to Zernio's CDN.
settings.media_dir.mkdir(parents=True, exist_ok=True)
app.mount("/media", StaticFiles(directory=settings.media_dir), name="media")

app.include_router(templates_router)
app.include_router(drafts_router)
app.include_router(assets_router)

app.add_middleware(
    CORSMiddleware,
    # ponytail: wide-open CORS is fine for a single-team internal tool on localhost.
    # Restrict to the deployed frontend origin when this leaves the laptop.
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def database_reachable() -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


@app.get("/health")
def health() -> dict:
    """Liveness plus what the app can actually reach.

    The frontend status page renders this directly, so an operator can see at a glance
    whether the database is up and which credentials are configured.

    `status` is derived, not the literal `"ok"` it used to be: the status row can render a
    failure (US-007), and a readout structurally incapable of saying anything but "fine" makes
    that capability unobservable. `database_reachable()` is called once and reused — twice
    would be two connection attempts that could disagree inside one response.

    ponytail: `degraded` is the only failure word, and credentials do not affect it. Missing
    credentials break one feature each and are already reported per key; the database is the
    one dependency without which nothing here answers at all.
    """
    reachable = database_reachable()
    return {
        "status": "ok" if reachable else "degraded",
        "database": reachable,
        "credentials": settings.configured(),
    }


# Sortable columns, named explicitly. An allow-list rather than trusting the query string:
# an unknown field is rejected, never silently ignored, because a silent fallback shows a
# different ranking than the one that was asked for.
SORTABLE = {
    "engaged_actions",
    "impressions",
    "reach",
    "likes",
    "comments",
    "shares",
    "saves",
    "clicks",
    "engagement_rate",
    "published_at",
}


@app.get("/posts")
def list_posts(
    session: SessionDep,
    limit: int = 500,
    platform: str | None = None,
    source: PostSource | None = None,
    account: str | None = None,
    since: date | None = None,
    until: date | None = None,
    template_family: str | None = None,
    sort: str = "engaged_actions",
    order: str = "desc",
) -> list[Post]:
    """The corpus, filtered and sorted. Ranked by the primary success measure by default."""
    if sort not in SORTABLE:
        raise HTTPException(
            status_code=422,
            detail=f"cannot sort by {sort!r}; try one of {sorted(SORTABLE)}",
        )

    statement = select(Post)
    if platform:
        statement = statement.where(Post.platform == platform)
    if source:
        statement = statement.where(Post.source == source)
    if account:
        # The only filter that separates the cohorts: Monte's scraped posts and the creator
        # reference posts are both MANUAL, so `source` cannot tell them apart. Exact
        # equality, matching `extraction._strongest_posts`, so the account the dashboard
        # calls a cohort is the same set of rows extraction reads.
        statement = statement.where(Post.account_username == account)
    if since:
        statement = statement.where(col(Post.published_at) >= datetime.combine(since, time.min))
    if until:
        statement = statement.where(col(Post.published_at) <= datetime.combine(until, time.max))
    if template_family:
        # Posts this app generated under some version of that template family. Anything
        # without lineage cannot match, which is correct — it is not evidence about it.
        late_ids = select(col(Draft.zernio_post_id)).where(
            (Draft.hook_family == template_family)
            | (Draft.structure_family == template_family)
            | (Draft.visual_family == template_family)
        )
        statement = statement.where(col(Post.late_post_id).in_(late_ids))

    column = getattr(Post, sort)
    statement = statement.order_by(
        col(column).asc() if order == "asc" else col(column).desc()
    ).limit(limit)
    return list(session.exec(statement).all())


class ManualPostIn(BaseModel):
    content: str
    author: str | None = None
    platform: str = "linkedin"
    engaged_actions: int = 0
    impressions: int = 0
    published_at: datetime | None = None
    note: str = ""


@app.post("/corpus/manual", status_code=201)
def add_external_post(session: SessionDep, payload: ManualPostIn) -> Post:
    """Add a post Zernio does not carry — a creator post, a paste, a screenshot's text.

    Extraction treats it as evidence like any other post. A Zernio sync never touches it.
    """
    try:
        post = add_manual_post(session, **payload.model_dump())
    except ManualPostRejected as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    session.commit()
    session.refresh(post)
    return post


class ScrapedLinkedInPost(BaseModel):
    urn: str
    url: str | None = None
    # Kept as text: this is the scrape's own JSON, parsed where the corpus parses every
    # other timestamp.
    published_at: str | None = None
    content: str = ""
    likes: int = 0
    comments: int = 0
    shares: int = 0
    media_urls: list[str] = []
    media_type: str | None = None


class LinkedInScrapeIn(BaseModel):
    account: str | None = None
    posts: list[ScrapedLinkedInPost]


@app.post("/corpus/linkedin")
def ingest_linkedin_scrape(
    session: SessionDep, payload: LinkedInScrapeIn, with_media: bool = False
) -> dict:
    """Fold a LinkedIn profile scrape into the corpus.

    Reaches what Zernio cannot: posts predating its history, and repost counts, which it
    reports as zero on every LinkedIn row. Matching is on normalised content, not the
    URN — see `upsert_linkedin_posts`.

    Media is opt-in so an ingest of text alone never touches the network.
    """
    result = upsert_linkedin_posts(
        session,
        [item.model_dump() for item in payload.posts],
        account=payload.account,
        with_media=with_media,
    )
    session.commit()
    return {"created": result.created, "updated": result.updated}


@app.get("/posts/{post_id}/history")
def post_history(session: SessionDep, post_id: int) -> list[MetricSnapshot]:
    """Every reading taken of this post, oldest first — the engagement curve."""
    statement = (
        select(MetricSnapshot)
        .where(MetricSnapshot.post_id == post_id)
        .order_by(col(MetricSnapshot.captured_at))
    )
    return list(session.exec(statement).all())


@app.get("/posts/{post_id}")
def get_post(post_id: int, session: SessionDep) -> Post:
    post = session.get(Post, post_id)
    if post is None:
        raise HTTPException(status_code=404, detail=f"no post {post_id}")
    return post


class ExcludeIn(BaseModel):
    excluded: bool


@app.post("/posts/{post_id}/exclude")
def set_excluded(post_id: int, payload: ExcludeIn, session: SessionDep) -> Post:
    """Hold a post out of template extraction, or let it back in.

    For posts that rank high for reasons that cannot repeat — a launch, a network firing
    once. The post stays in the corpus and keeps its metrics; it just stops being taught.
    """
    post = session.get(Post, post_id)
    if post is None:
        raise HTTPException(status_code=404, detail=f"no post {post_id}")
    post.excluded_from_extraction = payload.excluded
    session.add(post)
    session.commit()
    session.refresh(post)
    return post


# A verdict note is a reason, not an essay. Capped at the route because the column is an
# unbounded String — nothing below this line will refuse a 10,000-character note.
VERDICT_NOTE_MAX = 500


class VerdictIn(BaseModel):
    # Typed as the enum, so an unknown value is rejected with a 422 naming the three
    # allowed values rather than being coerced into one of them. What reaches the row is
    # always a `Verdict` member, never a bare string.
    #
    # `Field(...)` is what makes this **required but nullable**: `null` clears the ruling,
    # while an empty body or a misspelt key stays a 422. A bare `Verdict | None` would give the
    # field a default of `None` and turn both of those into a silent wipe of a human's
    # judgement — a worse bug than the one being fixed.
    verdict: Verdict | None = Field(...)
    note: str = ""


@app.post("/posts/{post_id}/verdict")
def set_verdict(post_id: int, payload: VerdictIn, session: SessionDep) -> Post:
    """Record a human's ruling on a post: worked, didnt, or mixed, plus why — or clear it.

    The only form of learning that is honest at n=1 — a verdict claims judgement, not
    statistics. Re-settable, because a human changes their mind once they have seen how a
    post aged; the later ruling replaces the earlier one and `verdict_at` moves with it.

    `{"verdict": null}` retracts instead, taking the note and the timestamp with it: a note is
    the reason *for a ruling*, and one left behind after the ruling is gone would keep teaching
    `generation.verdict_lessons`, which selects on the note being non-empty. The post returns
    to Inbox queue 4, whose predicate is already `went_live_at is not null and verdict is null`.

    ponytail: no verdict history and no audit log. One ruling per post is the real
    cardinality, and a history has no reader until two people use this app.
    """
    if len(payload.note) > VERDICT_NOTE_MAX:
        raise HTTPException(
            status_code=422,
            detail=(
                f"verdict note is {len(payload.note)} characters; "
                f"the cap is {VERDICT_NOTE_MAX}"
            ),
        )

    post = session.get(Post, post_id)
    if post is None:
        raise HTTPException(status_code=404, detail=f"no post {post_id}")

    cleared = payload.verdict is None
    post.verdict = payload.verdict
    post.verdict_note = "" if cleared else payload.note
    post.verdict_at = None if cleared else datetime.now(UTC)
    session.add(post)
    session.commit()
    session.refresh(post)
    return post


class InboxItem(BaseModel):
    """One thing waiting at a gate.

    `id` is the id of whatever that gate acts on — a template to review, a draft to push, a
    post to rule on — so the queue an item came from is also what says which route acts on it:
    `/templates/{id}/approve`, `/drafts/{id}/push`, `/posts/{id}/verdict`.
    """

    id: int
    # A handle for recognising the thing, not the thing itself. See `INBOX_LABEL_MAX`.
    label: str
    # When this item entered this gate, and how long it has sat there. **The age is the
    # point.** A count says a queue is non-empty; "waiting 6 days" says the circuit stalled,
    # which is the only thing that distinguishes work in progress from work forgotten.
    waiting_since: datetime
    age_days: int


class InboxQueue(BaseModel):
    """A gate, what is behind it, and nothing that could be mistaken for a score.

    Deliberately carries no rate, no mean and no ranking, and the queues are not comparable
    with each other: these are things waiting for a human, not a measure of how anything
    performed. Nothing in this response may be rendered as performance.
    """

    count: int
    items: list[InboxItem]


class Inbox(BaseModel):
    """The four human gates of the lineage circuit, in the order a post passes through them.

    The circuit has completed **zero** laps, and every one of its gates is invisible until
    somebody remembers to go looking for it. That is the whole reason this route exists.
    """

    proposals_awaiting_review: InboxQueue
    built_awaiting_push: InboxQueue
    pushed_awaiting_monte: InboxQueue
    published_awaiting_verdict: InboxQueue

    # Laps already finished — not a fifth queue, because nothing is waiting behind it. It is
    # the complement of queue 4 over the same join: a draft that went live whose post now
    # carries a verdict has been all the way round, generate → push → publish → rule.
    #
    # **It is a count of laps, never a score.** Nothing about it ranks or rates a template,
    # draft or post; it says only whether the machine has ever run end to end. Today it is 0,
    # and that 0 is the point — see the route.
    closed_circuits: int


# An inbox label identifies a row; it is not a preview. LinkedIn posts in this corpus average
# ~818 characters, and four queues of that would be prose with the queue buried in it.
INBOX_LABEL_MAX = 80


def _label(text: str) -> str:
    collapsed = " ".join(text.split())
    if len(collapsed) <= INBOX_LABEL_MAX:
        return collapsed
    return collapsed[: INBOX_LABEL_MAX - 1] + "…"


def _utc(value: datetime) -> datetime:
    """The same instant, always aware.

    Every datetime column in this schema is `timestamp without time zone`, so a value the app
    wrote as `datetime.now(UTC)` reads back naive once the row has round-tripped — and whether
    that has happened yet depends on when the ORM expired the object. One query can therefore
    yield both kinds, and comparing them raises. Normalised once, here, rather than at each
    use. Same hazard `test_publish_detection.utc_naive` documents, resolved the other way.
    """
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _queue(rows: list[tuple[int, str, datetime]]) -> InboxQueue:
    """Oldest first — the item that has waited longest is the one worth seeing.

    ponytail: no pagination and no limit. The largest queue today is 37 proposals, and these
    are gates a human is meant to empty, so a queue long enough to need paging is itself the
    finding. Add a `limit` kwarg like `/posts` has if one ever gets there.
    """
    now = datetime.now(UTC)
    items = [
        InboxItem(
            id=row_id,
            label=_label(label),
            waiting_since=since,
            # Whole days, floored, and never negative: a clock skew that put an item slightly
            # in the future would otherwise render as "waiting -1 days".
            age_days=max((now - _utc(since)).days, 0),
        )
        for row_id, label, since in sorted(rows, key=lambda row: _utc(row[2]))
    ]
    return InboxQueue(count=len(items), items=items)


@app.get("/inbox")
def inbox(session: SessionDep) -> Inbox:
    """Everything waiting on a human, and for how long.

    Four queues, one per gate in the lineage circuit. They are queues, not scores: no ordering
    between them means anything and nothing here implies that a template, draft or post
    performed well or badly.

    **Queue 4 is lineage-only** — posts this app produced, joined through `Draft`. Postgres
    says the alternative is untenable: 57 posts are published and 57 carry no verdict, while
    zero drafts have ever gone live, so "all published posts" would open with a 57-row backlog
    of Monte's own historical writing — none of it a gate anyone intends to clear — burying the
    circuit this page exists to make visible. `POST /posts/{id}/verdict` still accepts any
    post, so an operator who wants to rule on one of those can; it just is not a queue.

    Queues 2, 3 and 4 therefore partition the drafts by the two timestamps: not pushed, pushed
    but not live, live but not judged. No draft is ever in two of them, and there is no status
    column that could disagree.

    `closed_circuits` is the fifth thing here and the only one that is not a queue: laps
    already completed, which is queue 4's join with the verdict present instead of absent. It
    is **0** against today's database, and reporting that 0 is the point — four empty queues
    are equally consistent with a circuit that has never run and one that is fully cleared,
    and until this number existed nothing on the page told them apart.
    """
    # `latest_versions` filtered to PROPOSED — the same definition `GET /templates?status=`
    # now serves, so the page and the list endpoint cannot disagree about what is awaiting
    # review. Newest-version-only on purpose: a superseded proposal is not reviewable.
    proposals = [
        (template.id or 0, template.name, template.created_at)
        for template in latest_versions(session)
        if template.status is TemplateStatus.PROPOSED
    ]

    unpushed = session.exec(select(Draft).where(col(Draft.zernio_post_id).is_(None))).all()

    awaiting_monte = session.exec(
        select(Draft).where(
            col(Draft.zernio_post_id).is_not(None), col(Draft.went_live_at).is_(None)
        )
    ).all()

    # The join is `Draft.zernio_post_id == Post.late_post_id`. Not `Post.zernio_id` — the
    # create response's `_id` surfaces in analytics as `latePostId`, and matching on
    # `analytics._id` finds nothing. See `metrics.draft_for_post`.
    awaiting_verdict = session.exec(
        select(Post, Draft)
        .join(Draft, col(Draft.zernio_post_id) == col(Post.late_post_id))
        .where(col(Draft.went_live_at).is_not(None), col(Post.verdict).is_(None))
    ).all()

    # The same join and the same lineage predicate as queue 4 with `verdict` flipped, so the
    # two cannot disagree about what a lap is: a post is either still waiting on a ruling or
    # its circuit is closed. Deliberately not folded into one partitioned read — that would
    # rewrite a working query for no gain.
    #
    # ponytail: `len` over the join rows, not `count(distinct Draft.id)`. `zernio_post_id` and
    # `late_post_id` are one-to-one in practice — `push_draft` writes one, `stamp_published`
    # matches one — so a fan-out would be a data bug rather than a lap counted twice. Ceiling
    # if that ever stops holding: count distinct drafts, since N is defined over drafts.
    closed = session.exec(
        select(Post, Draft)
        .join(Draft, col(Draft.zernio_post_id) == col(Post.late_post_id))
        .where(col(Draft.went_live_at).is_not(None), col(Post.verdict).is_not(None))
    ).all()

    return Inbox(
        proposals_awaiting_review=_queue(proposals),
        built_awaiting_push=_queue(
            [(d.id or 0, d.idea or d.hook_text, d.created_at) for d in unpushed]
        ),
        pushed_awaiting_monte=_queue(
            # `pushed_at or created_at`: `push_draft` writes both together, but the column is
            # nullable and rows predating it exist, and an age of "unknown" would render as a
            # blank where the number is the whole signal. Falling back to when the draft was
            # built overstates nothing — it can only be older.
            [
                (d.id or 0, d.idea or d.hook_text, d.pushed_at or d.created_at)
                for d in awaiting_monte
            ]
        ),
        published_awaiting_verdict=_queue(
            # `went_live_at`, not `Post.published_at`: the predicate guarantees the former is
            # set, while a post found live carrying no timestamp has the latter null. The
            # `is not None` is what narrows the type — the WHERE above already guarantees it.
            [
                (p.id or 0, p.content, d.went_live_at)
                for p, d in awaiting_verdict
                if d.went_live_at is not None
            ]
        ),
        closed_circuits=len(closed),
    )


@app.post("/corpus/ingest")
def trigger_ingest(session: SessionDep, with_media: bool = True) -> dict:
    """Pull every post and its metrics from Zernio into the corpus.

    Two sources, because neither is the whole account. `/analytics` carries the metrics
    but only for a recent 50-row window; `/v1/posts` is the full history but reports no
    metrics. Analytics runs first so a post that has metrics is stored with them, and the
    history pass then adds only what the window missed.

    Safe to re-run: posts are upserted on Zernio's own id, and metrics move as posts
    accumulate engagement.
    """
    client = ZernioClient()
    try:
        payloads = client.fetch_posts()
        history = client.list_posts()
    except ZernioResponseError as exc:
        # A silently-empty response must not read as "no posts" — it means the request
        # was rejected in a way the API does not report as an error.
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    finally:
        client.close()

    result = ingest_posts(session, payloads, with_media=with_media)
    # ponytail: recovered posts arrive without their media. They exist so extraction can
    # read their text; fetching images for rows that carry no metrics can wait.
    recovered = ingest_history(session, history)
    session.commit()
    return {
        "fetched": len(payloads),
        "created": result.created,
        "updated": result.updated,
        "history_fetched": len(history),
        "history_recovered": recovered.created,
    }


@app.post("/metrics/sync")
def trigger_metrics_sync(session: SessionDep) -> dict:
    """Refresh every post's metrics now and append a snapshot for each.

    The scheduler does this periodically; this endpoint is for when you do not want to wait.
    """
    client = ZernioClient()
    try:
        result = sync_metrics(session, client)
    except ZernioResponseError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    finally:
        client.close()
    session.commit()
    return result


@app.get("/metrics/templates")
def template_scoreboard(session: SessionDep) -> list[dict]:
    """What each template version has actually done, with its sample count.

    Deliberately unranked — at this sample size a ranking would be fitting noise.
    """
    return [
        {
            "family": row.family,
            "version": row.version,
            "kind": row.kind,
            "name": row.name,
            "status": row.status,
            "sample_count": row.sample_count,
            "total_engaged_actions": row.total_engaged_actions,
            "total_impressions": row.total_impressions,
            "mean_engaged_actions": round(row.mean_engaged_actions, 2),
            "sufficient": row.sufficient,
            "min_sample_size": settings.min_sample_size,
        }
        for row in template_performance(session)
    ]
