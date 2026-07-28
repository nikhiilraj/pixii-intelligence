from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.config import settings
from app.db import engine

app = FastAPI(title="Pixii Intelligence", version="0.1.0")

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
