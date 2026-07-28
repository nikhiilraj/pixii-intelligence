from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date, datetime, time

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import text
from sqlmodel import col, select

from app.api_drafts import router as drafts_router
from app.api_templates import router as templates_router
from app.config import settings
from app.corpus import ManualPostRejected, add_manual_post, ingest_posts
from app.db import engine
from app.deps import SessionDep
from app.metrics import sync_metrics, template_performance
from app.models.draft import Draft
from app.models.metric import MetricSnapshot
from app.models.post import Post, PostSource
from app.scheduler import shutdown as stop_scheduler
from app.scheduler import start as start_scheduler
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
    """
    return {
        "status": "ok",
        "database": database_reachable(),
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


@app.post("/corpus/ingest")
def trigger_ingest(session: SessionDep, with_media: bool = True) -> dict:
    """Pull every post and its metrics from Zernio into the corpus.

    Safe to re-run: posts are upserted on Zernio's own id, and metrics move as posts
    accumulate engagement.
    """
    client = ZernioClient()
    try:
        payloads = client.fetch_posts()
    except ZernioResponseError as exc:
        # A silently-empty response must not read as "no posts" — it means the request
        # was rejected in a way the API does not report as an error.
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    finally:
        client.close()

    result = ingest_posts(session, payloads, with_media=with_media)
    session.commit()
    return {"fetched": len(payloads), "created": result.created, "updated": result.updated}


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
