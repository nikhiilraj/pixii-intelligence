import io
from collections.abc import Set as AbstractSet
from datetime import datetime

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.assets import assets_dir
from app.config import settings
from app.db import get_session
from app.deps import get_html_renderer, get_llm
from app.extraction import ExtractionError, propose_visuals
from app.main import app
from app.models.asset import Asset, AssetKind
from app.models.post import Post
from app.models.template import TemplateKind, TemplateStatus
from app.rendering import MissingSlotValue

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


def a_logo(session) -> Asset:
    """A real one-pixel asset on disk, so `data_uri` has bytes to embed.

    `assets_dir()` resolves from `settings.media_dir` per call, which the `isolated_media`
    fixture already points at `tmp_path` — so this writes nowhere near the real cache.
    """
    raw = png(1, 1)
    assets_dir().mkdir(parents=True, exist_ok=True)
    (assets_dir() / "logo.png").write_bytes(raw)
    asset = Asset(
        filename="logo.png", label="logo", kind=AssetKind.LOGO,
        width=1, height=1, sha256="deadbeef",
    )
    session.add(asset)
    session.flush()
    return asset


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

    propose_visuals(session, llm, FakeRenderer())

    assert "strong" in (llm.user or "")
    assert (llm.user or "").index("strong") < (llm.user or "").index("weak")
    assert len(llm.images) == 2


def test_a_post_with_no_image_is_not_in_the_sample(session):
    add_post(session, "wordy", 900)
    llm = FakeLLM()

    propose_visuals(session, llm, FakeRenderer())

    assert llm.images == []


def test_a_video_is_not_visual_evidence(session):
    add_post(session, "clip", 900, media=(b"not-an-image", ".mp4"))
    llm = FakeLLM()

    propose_visuals(session, llm, FakeRenderer())

    assert llm.images == []


def test_a_corpus_with_no_images_proposes_nothing_and_raises_nothing(session):
    """Empty sample returns [], matching propose_hooks and propose_structures.

    The spec's error table said ExtractionError here. Returning [] is what the two
    sibling extractors already do for an empty sample, and one extractor that raises
    where its siblings return empty is a difference an operator has to memorise.
    """
    llm = FakeLLM()

    assert propose_visuals(session, llm, FakeRenderer()) == []
    assert llm.user is None  # the model was never called


def test_an_image_only_post_is_still_evidence(session):
    """A post with no text carries no hook and is still a picture that worked.

    `_strongest_posts` excludes empty content because a hook cannot come from nothing.
    Visual extraction passes `require_content=False`; without that, the purest sample in
    the corpus is silently discarded.
    """
    add_post(session, "picture-only", 900, media=(png(), ".png"), content="   ")
    llm = FakeLLM()

    propose_visuals(session, llm, FakeRenderer())

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

    [template] = propose_visuals(session, FakeLLM(ONE_VISUAL), FakeRenderer())

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

    [template] = propose_visuals(session, FakeLLM(ONE_VISUAL), FakeRenderer())

    assert (template.body["width"], template.body["height"]) == (1080, 1080)


def test_dimensions_fall_back_when_the_source_is_ambiguous(session):
    add_post(session, "a", 900, media=(png(1080, 1080), ".png"))
    add_post(session, "b", 800, media=(png(1080, 1080), ".png"))
    two_sources = {"visuals": [{**ONE_VISUAL["visuals"][0], "source_post_ids": ["a", "b"]}]}

    [template] = propose_visuals(session, FakeLLM(two_sources), FakeRenderer())

    assert (template.body["width"], template.body["height"]) == (1080, 1350)


def test_an_invented_source_id_is_dropped(session):
    add_post(session, "strong", 900, media=(png(), ".png"))
    invented = {"visuals": [{**ONE_VISUAL["visuals"][0], "source_post_ids": ["strong", "made-up"]}]}

    [template] = propose_visuals(session, FakeLLM(invented), FakeRenderer())

    assert template.provenance == ["strong"]


def test_markup_referencing_an_undeclared_slot_is_dropped(session):
    """Left in, this raises MissingSlotValue in Studio — after a human approved it."""
    add_post(session, "strong", 900, media=(png(), ".png"))
    broken = {"visuals": [
        {**ONE_VISUAL["visuals"][0], "name": "broken", "html": "<h1>{headline}</h1><p>{ghost}</p>"},
        ONE_VISUAL["visuals"][0],
    ]}

    proposals = propose_visuals(session, FakeLLM(broken), FakeRenderer())

    assert [t.name for t in proposals] == ["ranked-bars"]


def test_a_declared_slot_missing_from_the_markup_is_dropped(session):
    add_post(session, "strong", 900, media=(png(), ".png"))
    orphan = {"visuals": [{
        **ONE_VISUAL["visuals"][0],
        "slots": [*ONE_VISUAL["visuals"][0]["slots"], {"name": "unused", "type": "text"}],
    }]}

    assert propose_visuals(session, FakeLLM(orphan), FakeRenderer()) == []


def test_a_response_without_a_visuals_list_is_an_extraction_error(session):
    add_post(session, "strong", 900, media=(png(), ".png"))

    with pytest.raises(ExtractionError):
        propose_visuals(session, FakeLLM({"layouts": []}), FakeRenderer())


def test_the_prompt_carries_the_brand_tokens(session):
    add_post(session, "strong", 900, media=(png(), ".png"))
    llm = FakeLLM(ONE_VISUAL)

    propose_visuals(session, llm, FakeRenderer())

    assert "#d65831" in (llm.system or "")


def test_a_visuals_entry_that_is_not_an_object_does_not_cost_its_siblings(session):
    add_post(session, "strong", 900, media=(png(), ".png"))
    malformed = {"visuals": ["not an object", ONE_VISUAL["visuals"][0]]}

    proposals = propose_visuals(session, FakeLLM(malformed), FakeRenderer())

    assert [t.name for t in proposals] == ["ranked-bars"]


def test_a_malformed_slots_list_does_not_cost_its_siblings(session):
    add_post(session, "strong", 900, media=(png(), ".png"))
    malformed = {"visuals": [
        {**ONE_VISUAL["visuals"][0], "name": "malformed", "slots": ["kicker", "headline"]},
        ONE_VISUAL["visuals"][0],
    ]}

    proposals = propose_visuals(session, FakeLLM(malformed), FakeRenderer())

    assert [t.name for t in proposals] == ["ranked-bars"]


def test_a_non_string_name_does_not_cost_its_siblings(session):
    add_post(session, "strong", 900, media=(png(), ".png"))
    malformed = {"visuals": [
        {**ONE_VISUAL["visuals"][0], "name": 5},
        ONE_VISUAL["visuals"][0],
    ]}

    proposals = propose_visuals(session, FakeLLM(malformed), FakeRenderer())

    assert "ranked-bars" in [t.name for t in proposals]


def test_a_non_list_source_post_ids_does_not_cost_its_siblings(session):
    add_post(session, "strong", 900, media=(png(), ".png"))
    malformed = {"visuals": [
        {**ONE_VISUAL["visuals"][0], "name": "malformed", "source_post_ids": 5},
        ONE_VISUAL["visuals"][0],
    ]}

    proposals = propose_visuals(session, FakeLLM(malformed), FakeRenderer())

    assert [t.name for t in proposals] == ["ranked-bars"]


def test_a_nested_source_post_id_does_not_cost_its_siblings(session):
    add_post(session, "strong", 900, media=(png(), ".png"))
    malformed = {"visuals": [
        {
            **ONE_VISUAL["visuals"][0],
            "name": "malformed",
            "source_post_ids": ["strong", ["nested"]],
        },
        ONE_VISUAL["visuals"][0],
    ]}

    proposals = propose_visuals(session, FakeLLM(malformed), FakeRenderer())

    assert [t.name for t in proposals] == ["malformed", "ranked-bars"]


def test_a_nan_slot_value_does_not_cost_its_siblings(session):
    """`json.loads` accepts NaN; Postgres JSONB does not, and a DataError kills the batch."""
    add_post(session, "strong", 900, media=(png(), ".png"))
    malformed = {"visuals": [
        {
            **ONE_VISUAL["visuals"][0],
            "name": "malformed",
            "slots": [
                {"name": "kicker", "type": "text", "example": float("nan")},
                {"name": "headline", "type": "text", "example": "AI did the shopping."},
            ],
        },
        ONE_VISUAL["visuals"][0],
    ]}

    proposals = propose_visuals(session, FakeLLM(malformed), FakeRenderer())

    assert [t.name for t in proposals] == ["ranked-bars"]


# Written as escapes, never as literal characters: a source file carrying a real NUL or a
# lone surrogate is unreadable in most editors and unsafe to move through a terminal.
NUL = "\x00"
LONE_SURROGATE = "\ud800"

# Deep enough that a recursive walk is guaranteed to fail — Python's recursion limit is
# 1000 frames and a recursive walk spends at least one per level — and far enough below
# the ~1490 that psycopg's own encoder manages here that Postgres stores it comfortably.
DEEP = 1000


def _deep_list(depth: int) -> list:
    value: list = []
    for _ in range(depth):
        value = [value]
    return value


def test_a_nul_in_a_slot_value_does_not_cost_its_siblings(session):
    add_post(session, "strong", 900, media=(png(), ".png"))
    malformed = {"visuals": [
        {**ONE_VISUAL["visuals"][0], "name": "malformed",
         "slots": [{"name": "kicker", "example": "a" + NUL}, {"name": "headline"}]},
        ONE_VISUAL["visuals"][0],
    ]}

    proposals = propose_visuals(session, FakeLLM(malformed), FakeRenderer())

    assert [t.name for t in proposals] == ["ranked-bars"]


def test_a_nul_in_a_slot_key_does_not_cost_its_siblings(session):
    """A key is as unstorable as a value, and only the key path reaches this one."""
    add_post(session, "strong", 900, media=(png(), ".png"))
    malformed = {"visuals": [
        {**ONE_VISUAL["visuals"][0], "name": "malformed",
         "slots": [{"name": "kicker", "note" + NUL: "x"}, {"name": "headline"}]},
        ONE_VISUAL["visuals"][0],
    ]}

    proposals = propose_visuals(session, FakeLLM(malformed), FakeRenderer())

    assert [t.name for t in proposals] == ["ranked-bars"]


def test_a_nul_in_the_html_does_not_cost_its_siblings(session):
    add_post(session, "strong", 900, media=(png(), ".png"))
    malformed = {"visuals": [
        {**ONE_VISUAL["visuals"][0], "name": "malformed",
         "html": ONE_VISUAL["visuals"][0]["html"] + NUL},
        ONE_VISUAL["visuals"][0],
    ]}

    proposals = propose_visuals(session, FakeLLM(malformed), FakeRenderer())

    assert [t.name for t in proposals] == ["ranked-bars"]


def test_a_nul_in_the_rationale_does_not_cost_its_siblings(session):
    """A distinct sink from `html`, even though both are str()-coerced into `body`."""
    add_post(session, "strong", 900, media=(png(), ".png"))
    malformed = {"visuals": [
        {**ONE_VISUAL["visuals"][0], "name": "malformed", "rationale": "why" + NUL},
        ONE_VISUAL["visuals"][0],
    ]}

    proposals = propose_visuals(session, FakeLLM(malformed), FakeRenderer())

    assert [t.name for t in proposals] == ["ranked-bars"]


def test_a_nul_in_the_name_does_not_cost_its_siblings(session):
    """`name` is a text column, not JSONB — psycopg refuses it before the wire."""
    add_post(session, "strong", 900, media=(png(), ".png"))
    malformed = {"visuals": [
        {**ONE_VISUAL["visuals"][0], "name": "malformed" + NUL},
        ONE_VISUAL["visuals"][0],
    ]}

    proposals = propose_visuals(session, FakeLLM(malformed), FakeRenderer())

    assert [t.name for t in proposals] == ["ranked-bars"]


def test_a_lone_surrogate_does_not_cost_its_siblings(session):
    """`"\\ud800"` is a legal JSON escape that decodes to a string UTF-8 cannot encode."""
    add_post(session, "strong", 900, media=(png(), ".png"))
    malformed = {"visuals": [
        {**ONE_VISUAL["visuals"][0], "name": "malformed",
         "slots": [{"name": "kicker", "example": LONE_SURROGATE}, {"name": "headline"}]},
        ONE_VISUAL["visuals"][0],
    ]}

    proposals = propose_visuals(session, FakeLLM(malformed), FakeRenderer())

    assert [t.name for t in proposals] == ["ranked-bars"]


def test_a_deeply_nested_slot_value_does_not_cost_its_siblings(session):
    """Postgres stores this one — the batch-killer would be the probe walking it.

    The other tests here prove the probe rejects what it must. This one proves it does not
    crash on what it must accept: a recursive walk over a 1000-deep value raises
    RecursionError, which is not a `_RejectedProposal` either. So this test fails not when
    the probe is removed but when it is rewritten recursively, which is the obvious
    simplification a later reader would reach for.
    """
    add_post(session, "strong", 900, media=(png(), ".png"))
    deep = {"visuals": [
        {**ONE_VISUAL["visuals"][0], "name": "deep",
         "slots": [{"name": "kicker", "example": _deep_list(DEEP)}, {"name": "headline"}]},
        ONE_VISUAL["visuals"][0],
    ]}

    proposals = propose_visuals(session, FakeLLM(deep), FakeRenderer())

    assert [t.name for t in proposals] == ["deep", "ranked-bars"]


class FakeRenderer:
    """Screenshots, or refuses the layouts named in `broken`."""

    # `AbstractSet`, not `set`: the brief's literal `set[str] = frozenset()` types the
    # parameter as mutable but defaults it to an immutable instance, which mypy rejects as
    # an incompatible default. `broken` is only ever read via `in`, so the read-only
    # supertype both `set` and `frozenset` satisfy is the honest type here.
    def __init__(self, broken: AbstractSet[str] = frozenset()):
        self.broken = broken
        self.rendered: list[str] = []

    def screenshot(self, html: str, width: int, height: int) -> bytes:
        self.rendered.append(html)
        if any(name in html for name in self.broken):
            raise MissingSlotValue("nothing to fill it with")
        return b"PNG"


def test_a_proposal_that_does_not_render_is_dropped(session):
    """A layout that fails is not a proposal — it is a trap with an approval on it."""
    add_post(session, "strong", 900, media=(png(), ".png"))
    two = {"visuals": [
        {**ONE_VISUAL["visuals"][0], "name": "bad", "html": "<h1>BOOM {headline}</h1>",
         "slots": [{"name": "headline", "type": "text", "example": "x"}]},
        ONE_VISUAL["visuals"][0],
    ]}

    proposals = propose_visuals(session, FakeLLM(two), FakeRenderer(broken={"BOOM"}))

    assert [t.name for t in proposals] == ["ranked-bars"]


def test_every_kept_proposal_was_rendered_once(session):
    add_post(session, "strong", 900, media=(png(), ".png"))
    renderer = FakeRenderer()

    propose_visuals(session, FakeLLM(ONE_VISUAL), renderer)

    assert len(renderer.rendered) == 1
    assert "PRIME DAY 2026" in renderer.rendered[0]


def test_a_logo_role_is_pinned_to_the_configured_asset(session, monkeypatch):
    """A real asset, not a bare id: the render gate resolves the pin, and a made-up id
    would be caught there rather than proving anything about the pin itself."""
    logo = a_logo(session)
    monkeypatch.setattr(settings, "brand_logo_asset_id", logo.id)
    add_post(session, "strong", 900, media=(png(), ".png"))
    with_logo = {"visuals": [{
        "name": "with-logo",
        "html": "<h1>{headline}</h1><img src='{mark}'>",
        "slots": [
            {"name": "headline", "type": "text", "example": "x"},
            {"name": "mark", "type": "image_url", "role": "logo", "example": "https://x/y.png"},
        ],
        "source_post_ids": ["strong"],
    }]}

    [template] = propose_visuals(session, FakeLLM(with_logo), FakeRenderer())

    mark = next(s for s in template.slots if s["name"] == "mark")
    assert mark["default_asset_id"] == logo.id


def test_a_slot_merely_named_logo_is_not_pinned(session, monkeypatch):
    """Pinned on the declared role, never the name.

    `left_image_url` reads as an asset and `hero` does not — the same reason
    `asset_slots` reads `type` rather than guessing.
    """
    monkeypatch.setattr(settings, "brand_logo_asset_id", 42)
    add_post(session, "strong", 900, media=(png(), ".png"))
    named = {"visuals": [{
        "name": "named-only",
        "html": "<h1>{headline}</h1><img src='{logo}'>",
        "slots": [
            {"name": "headline", "type": "text", "example": "x"},
            {"name": "logo", "type": "image_url", "example": "https://x/y.png"},
        ],
        "source_post_ids": ["strong"],
    }]}

    [template] = propose_visuals(session, FakeLLM(named), FakeRenderer())

    assert "default_asset_id" not in next(s for s in template.slots if s["name"] == "logo")


def test_no_configured_logo_leaves_the_slot_unpinned_and_raises_nothing(session, monkeypatch):
    """A first run happens before anyone has uploaded a logo."""
    monkeypatch.setattr(settings, "brand_logo_asset_id", None)
    add_post(session, "strong", 900, media=(png(), ".png"))
    with_logo = {"visuals": [{
        "name": "with-logo",
        "html": "<h1>{headline}</h1><img src='{mark}'>",
        "slots": [
            {"name": "headline", "type": "text", "example": "x"},
            {"name": "mark", "type": "image_url", "role": "logo", "example": "https://x/y.png"},
        ],
        "source_post_ids": ["strong"],
    }]}

    [template] = propose_visuals(session, FakeLLM(with_logo), FakeRenderer())

    assert "default_asset_id" not in next(s for s in template.slots if s["name"] == "mark")


def test_a_pin_naming_a_missing_asset_is_dropped_rather_than_offered(session, monkeypatch):
    """A `brand_logo_asset_id` pointing nowhere is caught at the gate, before a human can
    approve a template that will only fail once someone actually tries to generate from it.
    """
    monkeypatch.setattr(settings, "brand_logo_asset_id", 999999)
    add_post(session, "strong", 900, media=(png(), ".png"))
    with_logo = {"visuals": [{
        "name": "with-logo",
        "html": "<h1>{headline}</h1><img src='{mark}'>",
        "slots": [
            {"name": "headline", "type": "text", "example": "x"},
            {"name": "mark", "type": "image_url", "role": "logo", "example": "https://x/y.png"},
        ],
        "source_post_ids": ["strong"],
    }]}

    assert propose_visuals(session, FakeLLM(with_logo), FakeRenderer()) == []


def test_a_render_that_raises_an_http_status_error_does_not_cost_its_siblings(session):
    """`CloudflareRenderer.screenshot` calls `raise_for_status()` on any non-429 response,
    so a 500 on one proposal must not be an exception type the render gate lets through
    to kill the batch — the same failure mode `MissingSlotValue` and `UnresolvableAsset`
    already guard against, from a different layer.
    """
    add_post(session, "strong", 900, media=(png(), ".png"))
    two = {"visuals": [
        {**ONE_VISUAL["visuals"][0], "name": "flaky", "html": "<h1>FLAKY {headline}</h1>",
         "slots": [{"name": "headline", "type": "text", "example": "x"}]},
        ONE_VISUAL["visuals"][0],
    ]}

    class FlakyRenderer:
        def screenshot(self, html: str, width: int, height: int) -> bytes:
            if "FLAKY" in html:
                request = httpx.Request("POST", "https://example.invalid")
                response = httpx.Response(500, request=request)
                raise httpx.HTTPStatusError("boom", request=request, response=response)
            return b"PNG"

    proposals = propose_visuals(session, FakeLLM(two), FlakyRenderer())

    assert [t.name for t in proposals] == ["ranked-bars"]


def test_extract_visuals_route_returns_proposals(session):
    add_post(session, "strong", 900, media=(png(), ".png"))
    session.commit()

    # `TestClient(app)` otherwise gets its own `get_session()` — a fresh connection that
    # cannot see this test's row without a real commit crossing connections. Overriding it
    # to the test's own session is what `client_with()` does in test_api_templates.py; the
    # brief's version of this test omitted it and returned no proposals, because the
    # route's fresh connection never saw "strong" at all.
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_llm] = lambda: FakeLLM(ONE_VISUAL)
    app.dependency_overrides[get_html_renderer] = lambda: FakeRenderer()
    try:
        response = TestClient(app).post("/templates/extract/visuals")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 201
    assert [t["name"] for t in response.json()] == ["ranked-bars"]
    assert response.json()[0]["status"] == "proposed"
