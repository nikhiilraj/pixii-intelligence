import logging
from pathlib import Path
from urllib.parse import urlparse

import httpx

from app.config import settings
from app.models.post import Post

log = logging.getLogger("pixii.media")

# Extensions we expect from Zernio's media host. Anything else is stored without a
# suffix rather than trusting an arbitrary path segment as a file type.
_KNOWN_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".mp4", ".mov", ".webm"}


def _source_url(post: Post) -> str | None:
    for item in post.media_items:
        url = item.get("url")
        if url:
            return str(url)
    return post.thumbnail_url


def _filename(zernio_id: str, url: str) -> str:
    suffix = Path(urlparse(url).path).suffix.lower()
    return f"{zernio_id}{suffix}" if suffix in _KNOWN_SUFFIXES else zernio_id


def download_post_media(
    post: Post,
    transport: httpx.BaseTransport | None = None,
    media_dir: Path | None = None,
) -> str | None:
    """Store a post's media locally and return its filename, or None.

    Media is supporting material for studying visual patterns. A post is still complete
    without it, so every failure here is logged and swallowed rather than raised — losing
    an image must never cost us the post it belongs to.
    """
    directory = media_dir or settings.media_dir
    url = _source_url(post)
    if not url:
        return None

    name = _filename(post.zernio_id, url)
    destination = directory / name
    if destination.exists():
        return name

    try:
        with httpx.Client(transport=transport, timeout=60.0, follow_redirects=True) as client:
            response = client.get(url)
            response.raise_for_status()
            directory.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(response.content)
    except Exception as exc:
        log.warning("media download failed for post %s (%s): %s", post.zernio_id, url, exc)
        return None

    return name
