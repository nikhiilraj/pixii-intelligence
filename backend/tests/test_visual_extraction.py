import io
from datetime import datetime

from PIL import Image

from app.config import settings
from app.extraction import propose_visuals
from app.models.post import Post

CURRENT = datetime(2026, 6, 1)


class FakeLLM:
    """Records what it was handed, including the images, and replays a canned response."""

    def __init__(self, response: dict | None = None):
        self.response = response or {"visuals": []}
        self.system: str | None = None
        self.user: str | None = None
        self.images: list[bytes] = []

    def complete_json(self, system: str, user: str, images=()) -> dict:
        self.system, self.user, self.images = system, user, list(images)
        return self.response


def png(width: int = 1080, height: int = 1350) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), "white").save(buffer, format="PNG")
    return buffer.getvalue()


def add_post(session, zid, engaged, *, media=None, content="Body.", author=None):
    """A post, with its media written to `settings.media_dir` when bytes are supplied."""
    filename = None
    if media is not None:
        raw, suffix = media
        filename = f"{zid}{suffix}"
        settings.media_dir.mkdir(parents=True, exist_ok=True)
        (settings.media_dir / filename).write_bytes(raw)
    post = Post(
        zernio_id=zid,
        platform="linkedin",
        account_username=author or settings.voice_account,
        content=content,
        engaged_actions=engaged,
        published_at=CURRENT,
        local_media_path=filename,
    )
    session.add(post)
    session.flush()
    return post


def test_sample_is_image_posts_strongest_first(session):
    add_post(session, "weak", 10, media=(png(), ".png"))
    add_post(session, "strong", 900, media=(png(), ".png"))
    llm = FakeLLM()

    propose_visuals(session, llm)

    assert "strong" in (llm.user or "")
    assert (llm.user or "").index("strong") < (llm.user or "").index("weak")
    assert len(llm.images) == 2


def test_a_post_with_no_image_is_not_in_the_sample(session):
    add_post(session, "wordy", 900)
    llm = FakeLLM()

    propose_visuals(session, llm)

    assert llm.images == []


def test_a_video_is_not_visual_evidence(session):
    add_post(session, "clip", 900, media=(b"not-an-image", ".mp4"))
    llm = FakeLLM()

    propose_visuals(session, llm)

    assert llm.images == []


def test_a_corpus_with_no_images_proposes_nothing_and_raises_nothing(session):
    """Empty sample returns [], matching propose_hooks and propose_structures.

    The spec's error table said ExtractionError here. Returning [] is what the two
    sibling extractors already do for an empty sample, and one extractor that raises
    where its siblings return empty is a difference an operator has to memorise.
    """
    llm = FakeLLM()

    assert propose_visuals(session, llm) == []
    assert llm.user is None  # the model was never called


def test_an_image_only_post_is_still_evidence(session):
    """A post with no text carries no hook and is still a picture that worked.

    `_strongest_posts` excludes empty content because a hook cannot come from nothing.
    Visual extraction passes `require_content=False`; without that, the purest sample in
    the corpus is silently discarded.
    """
    add_post(session, "picture-only", 900, media=(png(), ".png"), content="   ")
    llm = FakeLLM()

    propose_visuals(session, llm)

    assert len(llm.images) == 1
