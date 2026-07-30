"""Assets reaching a render as embedded base64, on all three paths that render.

Every renderer here is `renderer_capturing` — a real `CloudflareRenderer` over an
`httpx.MockTransport` that keeps the posted body. `FakeRenderer` (`test_generation.py`)
throws the `html` argument away, so under it a slice that embedded nothing at all would
pass. Nothing in this file touches the network or the real asset directory.
"""

import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlmodel import Session

from app.assets import (
    UnresolvableAsset,
    data_uri,
    resolve_asset_values,
    sha256_of,
    store_image,
)
from app.config import settings
from app.db import get_session
from app.deps import get_html_renderer
from app.generation import regenerate_visual
from app.main import app
from app.models.asset import Asset, AssetKind
from app.models.draft import Draft
from app.models.template import Template, TemplateKind
from app.rendering import render_visual
from tests.test_rendering import renderer_capturing

# Two image slots and one text slot, as `stat-hero` v2 (template 1101) actually is.
STAT_HERO = (
    '<div><span>{big_number}</span><img src="{left_image_url}">'
    '<img src="{right_image_url}"></div>'
)


def visual(session: Session, **body) -> Template:
    payload = {"renderer": "html", "html": STAT_HERO}
    payload.update(body)
    template = Template(
        family_id="stat-hero",
        kind=TemplateKind.VISUAL,
        name="stat-hero",
        body=payload,
        slots=[
            {"name": "big_number", "type": "text", "example": "$325M"},
            {"name": "left_image_url", "type": "image_url", "example": "https://…/product.png"},
            {"name": "right_image_url", "type": "image_url", "example": "https://…/logo.png"},
        ],
    )
    session.add(template)
    session.flush()
    return template


@pytest.fixture
def asset(session: Session, tmp_path, monkeypatch) -> Asset:
    """A real 8x8 PNG on disk, in a temporary media directory.

    The `session` fixture rolls its transaction back; nothing rolls back a file, so the
    media directory is redirected the way `test_api_assets.py` does it.
    """
    monkeypatch.setattr(settings, "media_dir", tmp_path)
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), "orange").save(buffer, format="PNG")
    raw = buffer.getvalue()

    digest = sha256_of(raw)
    filename, width, height = store_image(raw, digest, "wordmark.png", "image/png")
    row = Asset(
        filename=filename,
        label="Pixii wordmark",
        kind=AssetKind.LOGO,
        width=width,
        height=height,
        sha256=digest,
    )
    session.add(row)
    session.flush()
    return row


def test_an_asset_id_is_embedded_in_the_markup_that_reaches_the_renderer(session, asset):
    captured: dict = {}
    template = visual(session)
    values = {
        "big_number": "$325M",
        "left_image_url": str(asset.id),
        "right_image_url": str(asset.id),
    }

    render_visual(
        template, resolve_asset_values(session, template, values), renderer_capturing(captured)
    )

    # Byte-identical, not merely "contains base64": `fill()` escapes slot values, and this
    # assertion is what proves the escaping left the payload alone. A substring check for
    # "base64" would still pass on a mangled URI.
    assert data_uri(asset) in captured["html"]
    assert captured["html"].count("data:image/png;base64,") == 2
    assert "{left_image_url}" not in captured["html"]


def test_the_embedded_bytes_are_the_stored_file(session, asset):
    import base64

    from app.assets import asset_path

    encoded = data_uri(asset).removeprefix("data:image/png;base64,")

    assert base64.b64decode(encoded) == asset_path(asset).read_bytes()


def test_resolution_returns_a_new_dict_and_leaves_the_asset_id_in_place(session, asset):
    values = {"big_number": "$325M", "left_image_url": str(asset.id)}

    resolved = resolve_asset_values(session, visual(session), values)

    # If resolution mutated its input, the id would be gone and the *next* render — a
    # regenerate, a preview — would be handed a data URI where an id belongs.
    assert values["left_image_url"] == str(asset.id)
    assert resolved is not values
    assert resolved["left_image_url"].startswith("data:image/png;base64,")
    assert resolved["big_number"] == "$325M"


def draft_using(session: Session, asset: Asset) -> Draft:
    draft = Draft(
        idea="a nine figure exit",
        hook_family="hook",
        structure_family="structure",
        visual_family="stat-hero",
        visual_version=1,
        hook_text="A 9-figure exit.",
        visual_values={
            "big_number": "$325M",
            "left_image_url": str(asset.id),
            "right_image_url": str(asset.id),
        },
    )
    session.add(draft)
    session.flush()
    return draft


def test_regenerating_a_visual_embeds_the_assets_every_time(session, asset):
    """`regenerate_visual` takes no `values` argument — it must read them off the draft.

    Twice, deliberately. Resolution that wrote its result back onto `draft.visual_values`
    would pass the first call and fail the second, with an asset id replaced by the data
    URI it resolved to.
    """
    visual(session)
    draft = draft_using(session, asset)
    expected = data_uri(asset)

    for _ in range(2):
        captured: dict = {}
        regenerate_visual(session, draft, renderer_capturing(captured, png=b"IMAGE"))

        assert draft.visual_error is None
        assert draft.visual_image == b"IMAGE"
        assert expected in captured["html"]
        assert draft.visual_values["left_image_url"] == str(asset.id)


def test_generating_a_draft_resolves_through_the_same_function(session, asset):
    """The `generate_draft` path, exercised at its own seam.

    `generate_draft` reaches resolution through `_draw_visual`, the same function
    `regenerate_visual` uses, so this asserts the draft-shaped call rather than
    re-asserting the LLM plumbing above it.
    """
    from app.generation import _draw_visual

    captured: dict = {}
    template = visual(session)
    draft = draft_using(session, asset)

    _draw_visual(session, draft, template, renderer_capturing(captured))

    assert draft.visual_error is None
    assert data_uri(asset) in captured["html"]


def test_an_unfilled_image_slot_still_reports_the_slot_by_name(session):
    """Absent stays absent. The operator-legible failure is `MissingSlotValue`.

    This is today's `generate_draft` outcome: `_written_values` drops the image_url values
    a model volunteers, so nothing fills the slot until the picker does (US-009). It must
    fail as it always has, not as an unresolvable-asset mystery.
    """
    visual(session)
    draft = Draft(
        idea="i",
        hook_family="hook",
        structure_family="structure",
        visual_family="stat-hero",
        visual_values={"big_number": "$325M"},
    )
    session.add(draft)
    session.flush()

    regenerate_visual(session, draft, renderer_capturing({}))

    assert draft.visual_image is None
    assert draft.visual_error is not None
    assert "MissingSlotValue" in draft.visual_error
    assert "left_image_url" in draft.visual_error


def test_a_raw_svg_data_uri_is_rejected_rather_than_silently_mangled(session):
    raw_svg = 'data:image/svg+xml,<svg xmlns="http://www.w3.org/2000/svg"/>'

    with pytest.raises(UnresolvableAsset) as caught:
        resolve_asset_values(session, visual(session), {"left_image_url": raw_svg})

    # `fill()` escapes it to `&lt;svg`, the browser renders an empty box and the render
    # reports success. Nothing about the message may suggest widening `escape=False`.
    assert "empty box" in str(caught.value)


def test_a_base64_data_uri_is_passed_through(session):
    already = "data:image/png;base64,iVBORw0KGgo="

    resolved = resolve_asset_values(session, visual(session), {"left_image_url": already})

    assert resolved["left_image_url"] == already


def test_a_public_url_is_left_for_the_remote_browser_to_fetch(session):
    """A template's `example` values are URLs, and previewing one worked before assets."""
    resolved = resolve_asset_values(
        session, visual(session), {"left_image_url": "https://example.test/logo.png"}
    )

    assert resolved["left_image_url"] == "https://example.test/logo.png"


def test_a_local_media_path_is_rejected(session):
    """The renderer is remote. `/media/...` resolves to nothing from Cloudflare."""
    with pytest.raises(UnresolvableAsset):
        resolve_asset_values(session, visual(session), {"left_image_url": "/media/assets/x.png"})


def test_an_asset_id_that_is_not_in_the_library_is_rejected(session):
    with pytest.raises(UnresolvableAsset) as caught:
        resolve_asset_values(session, visual(session), {"left_image_url": "9999"})

    assert "9999" in str(caught.value)


def test_an_asset_whose_file_is_gone_is_rejected_not_a_crash(session, asset):
    from app.assets import asset_path

    asset_path(asset).unlink()

    with pytest.raises(UnresolvableAsset):
        resolve_asset_values(session, visual(session), {"left_image_url": str(asset.id)})


def test_a_slot_carrying_no_type_key_is_left_alone(session):
    """Template 556's slots have no `type` key at all. Reading one must not raise."""
    untyped = Template(
        family_id="stat-hero-v1",
        kind=TemplateKind.VISUAL,
        name="stat-hero",
        body={"renderer": "html", "html": STAT_HERO},
        slots=[
            {"name": "big_number", "example": "$325M"},
            {"name": "left_image_url", "example": "https://…/product.png"},
        ],
    )
    session.add(untyped)
    session.flush()
    values = {"big_number": "$325M", "left_image_url": "image CTR"}

    assert resolve_asset_values(session, untyped, values) == values


def test_an_ai_template_gets_no_embedded_base64(session, asset):
    """An `ai` template's values become prose in a prompt, not markup."""
    prompt_template = Template(
        family_id="lifestyle",
        kind=TemplateKind.VISUAL,
        name="lifestyle-scene",
        body={"renderer": "ai", "prompt": "a scene of {subject} beside {left_image_url}"},
        slots=[
            {"name": "subject", "type": "text"},
            {"name": "left_image_url", "type": "image_url"},
        ],
    )
    session.add(prompt_template)
    session.flush()
    values = {"subject": "a ceramic jar", "left_image_url": str(asset.id)}

    assert resolve_asset_values(session, prompt_template, values) == values


class StubRenderer:
    """Captures the markup the API path renders, and answers with recognisable bytes."""

    def __init__(self) -> None:
        self.html = ""

    def screenshot(self, html: str, width: int, height: int) -> bytes:
        self.html = html
        return b"PNGBYTES"


def client_with(session: Session, renderer: StubRenderer) -> TestClient:
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_html_renderer] = lambda: renderer
    return TestClient(app)


def test_previewing_an_image_template_returns_an_image_not_a_422(session, asset):
    """The third consumer. `preview_visual` takes values straight from the request body."""
    template = visual(session)
    renderer = StubRenderer()

    response = client_with(session, renderer).post(
        f"/templates/{template.id}/preview",
        json={
            "big_number": "$325M",
            "left_image_url": str(asset.id),
            "right_image_url": str(asset.id),
        },
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content == b"PNGBYTES"
    assert data_uri(asset) in renderer.html
    app.dependency_overrides.clear()


def test_previewing_with_a_raw_svg_is_a_422_and_never_reaches_the_renderer(session):
    template = visual(session)
    renderer = StubRenderer()

    response = client_with(session, renderer).post(
        f"/templates/{template.id}/preview",
        json={
            "big_number": "$325M",
            "left_image_url": 'data:image/svg+xml,<svg xmlns="http://www.w3.org/2000/svg"/>',
            "right_image_url": "https://example.test/logo.png",
        },
    )

    assert response.status_code == 422
    assert renderer.html == ""
    app.dependency_overrides.clear()
