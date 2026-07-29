#!/usr/bin/env python3
"""Load the parsed creator-inspiration archive into the corpus.

Run with the backend's own interpreter, from `backend/`, so `app` and the `.env` beside
it resolve:

    cd backend && .venv/bin/python ../scripts/ingest_inspiration.py

Insert-only and keyed on each post's position in the document, so a second run creates
nothing and prints `created=0`. Images are copied out of the extraction directory, never
re-downloaded: the LinkedIn CDN addresses the document notes have expired.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.corpus import ingest_inspiration_posts
from app.db import engine
from sqlmodel import Session

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / ".scratch" / "corpus-widening" / "creator-inspiration.json"
IMAGES = DATA.parent / "creator-images"

# Fixed, not derived from the document's filename. The id has to survive a rename or a
# re-parse of the source .docx — derive it from anything mutable and a re-run duplicates
# all 14 rows instead of creating none.
DOC_SLUG = "linkedin-archive"


def main() -> int:
    posts = json.loads(DATA.read_text())["posts"]
    with Session(engine) as session:
        result = ingest_inspiration_posts(
            session, posts, doc_slug=DOC_SLUG, images_dir=IMAGES
        )
        session.commit()
    print(f"read={len(posts)} created={result.created} skipped={result.skipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
