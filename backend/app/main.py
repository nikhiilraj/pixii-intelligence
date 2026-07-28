from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlmodel import desc, select

from app.api_drafts import router as drafts_router
from app.api_templates import router as templates_router
from app.config import settings
from app.corpus import ingest_posts
from app.db import engine
from app.deps import SessionDep
from app.metrics import sync_metrics, template_performance
from app.models.post import Post
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


@app.get("/posts")
def list_posts(session: SessionDep, limit: int = 200) -> list[Post]:
    """The corpus, ranked by the primary success measure."""
    statement = select(Post).order_by(desc(Post.engaged_actions)).limit(limit)
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
        }
        for row in template_performance(session)
    ]
