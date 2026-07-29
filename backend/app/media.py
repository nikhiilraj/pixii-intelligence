import logging
import mimetypes
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


def _url_suffix(url: str) -> str:
    suffix = Path(urlparse(url).path).suffix.lower()
    return suffix if suffix in _KNOWN_SUFFIXES else ""


def _suffix(url: str, content_type: str | None) -> str:
    """The file type, from the URL if it says, otherwise from what the server sent.

    `media.licdn.com` serves images from paths carrying no extension at all. Stored
    suffixless, StaticFiles serves them as `application/octet-stream` and no browser
    renders them.
    """
    from_url = _url_suffix(url)
    if from_url:
        return from_url
    guessed = mimetypes.guess_extension((content_type or "").split(";")[0].strip()) or ""
    # `application/octet-stream` guesses `.bin` — a file type only in the loosest sense.
    return guessed if guessed in _KNOWN_SUFFIXES else ""


def _cached(directory: Path, zernio_id: str) -> str | None:
    """The stored file for this post, whatever suffix it ended up with."""
    for name in (f"{zernio_id}{suffix}" for suffix in ("", *_KNOWN_SUFFIXES)):
        if (directory / name).exists():
            return name
    return None


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

    cached = _cached(directory, post.zernio_id)
    if cached:
        return cached

    try:
        with httpx.Client(transport=transport, timeout=60.0, follow_redirects=True) as client:
            response = client.get(url)
            response.raise_for_status()
            # The suffix can depend on the response, so the name is settled only here.
            name = f"{post.zernio_id}{_suffix(url, response.headers.get('content-type'))}"
            directory.mkdir(parents=True, exist_ok=True)
            (directory / name).write_bytes(response.content)
    except Exception as exc:
        log.warning("media download failed for post %s (%s): %s", post.zernio_id, url, exc)
        return None

    return name
