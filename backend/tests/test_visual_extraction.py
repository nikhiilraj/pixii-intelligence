import io
from datetime import datetime

import pytest
from PIL import Image

from app.config import settings
from app.extraction import ExtractionError, propose_visuals
from app.models.post import Post
from app.models.template import TemplateKind, TemplateStatus

CURRENT = datetime(2026, 6, 1)


@pytest.fixture(autouse=True)
def isolated_media(tmp_path, monkeypatch):
    """Never write into the real media cache.

    `settings.media_dir` is the live directory `download_post_media` fills, and
    `_cached` resolves a post's file by scanning it for `{zernio_id}{suffix}` — so a
    leftover fixture file named like a real post id would be served as that post's
    media. The session fixture rolls back rows and does nothing to the filesystem.
    """
    monkeypatch.setattr(settings, "media_dir", tmp_path)


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


ONE_VISUAL = {
    "visuals": [{
        "name": "ranked-bars",
        "html": "<div>{kicker}</div><h1>{headline}</h1>",
        "slots": [
            {"name": "kicker", "type": "text", "example": "PRIME DAY 2026"},
            {"name": "headline", "type": "text", "example": "AI did the shopping."},
        ],
        "rationale": "the number is the picture",
        "source_post_ids": ["strong"],
    }]
}


def test_a_proposal_becomes_a_proposed_visual_template(session):
    add_post(session, "strong", 900, media=(png(), ".png"))

    [template] = propose_visuals(session, FakeLLM(ONE_VISUAL))

    assert template.kind is TemplateKind.VISUAL
    assert template.status is TemplateStatus.PROPOSED
    assert template.body["renderer"] == "html"
    assert template.provenance == ["strong"]


def test_dimensions_come_from_the_source_image(session):
    """The corpus is not one size — 1080x1350 and 1080x1080 both appear.

    A model asked to state a size states a plausible one. The image is in hand, so it is
    measured instead.
    """
    add_post(session, "strong", 900, media=(png(1080, 1080), ".png"))

    [template] = propose_visuals(session, FakeLLM(ONE_VISUAL))

    assert (template.body["width"], template.body["height"]) == (1080, 1080)


def test_dimensions_fall_back_when_the_source_is_ambiguous(session):
    add_post(session, "a", 900, media=(png(1080, 1080), ".png"))
    add_post(session, "b", 800, media=(png(1080, 1080), ".png"))
    two_sources = {"visuals": [{**ONE_VISUAL["visuals"][0], "source_post_ids": ["a", "b"]}]}

    [template] = propose_visuals(session, FakeLLM(two_sources))

    assert (template.body["width"], template.body["height"]) == (1080, 1350)


def test_an_invented_source_id_is_dropped(session):
    add_post(session, "strong", 900, media=(png(), ".png"))
    invented = {"visuals": [{**ONE_VISUAL["visuals"][0], "source_post_ids": ["strong", "made-up"]}]}

    [template] = propose_visuals(session, FakeLLM(invented))

    assert template.provenance == ["strong"]


def test_markup_referencing_an_undeclared_slot_is_dropped(session):
    """Left in, this raises MissingSlotValue in Studio — after a human approved it."""
    add_post(session, "strong", 900, media=(png(), ".png"))
    broken = {"visuals": [
        {**ONE_VISUAL["visuals"][0], "name": "broken", "html": "<h1>{headline}</h1><p>{ghost}</p>"},
        ONE_VISUAL["visuals"][0],
    ]}

    proposals = propose_visuals(session, FakeLLM(broken))

    assert [t.name for t in proposals] == ["ranked-bars"]


def test_a_declared_slot_missing_from_the_markup_is_dropped(session):
    add_post(session, "strong", 900, media=(png(), ".png"))
    orphan = {"visuals": [{
        **ONE_VISUAL["visuals"][0],
        "slots": [*ONE_VISUAL["visuals"][0]["slots"], {"name": "unused", "type": "text"}],
    }]}

    assert propose_visuals(session, FakeLLM(orphan)) == []


def test_a_response_without_a_visuals_list_is_an_extraction_error(session):
    add_post(session, "strong", 900, media=(png(), ".png"))

    with pytest.raises(ExtractionError):
        propose_visuals(session, FakeLLM({"layouts": []}))


def test_the_prompt_carries_the_brand_tokens(session):
    add_post(session, "strong", 900, media=(png(), ".png"))
    llm = FakeLLM(ONE_VISUAL)

    propose_visuals(session, llm)

    assert "#d65831" in (llm.system or "")


def test_a_visuals_entry_that_is_not_an_object_does_not_cost_its_siblings(session):
    add_post(session, "strong", 900, media=(png(), ".png"))
    malformed = {"visuals": ["not an object", ONE_VISUAL["visuals"][0]]}

    proposals = propose_visuals(session, FakeLLM(malformed))

    assert [t.name for t in proposals] == ["ranked-bars"]


def test_a_malformed_slots_list_does_not_cost_its_siblings(session):
    add_post(session, "strong", 900, media=(png(), ".png"))
    malformed = {"visuals": [
        {**ONE_VISUAL["visuals"][0], "name": "malformed", "slots": ["kicker", "headline"]},
        ONE_VISUAL["visuals"][0],
    ]}

    proposals = propose_visuals(session, FakeLLM(malformed))

    assert [t.name for t in proposals] == ["ranked-bars"]
