import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.config import settings
from app.db import get_session
from app.deps import get_html_renderer, get_image_renderer
from app.main import app
from app.models.asset import Asset, AssetKind
from app.models.template import TemplateKind
from app.templates import create_template


@pytest.fixture(autouse=True)
def isolated_media(tmp_path, monkeypatch):
    """Never write into the real media cache.

    `assets_dir()` resolves under `settings.media_dir`, and `a_logo` below writes a real
    file there. The `session` fixture rolls back rows and does nothing to the filesystem —
    see `test_visual_extraction.isolated_media`, whose pattern this copies.
    """
    monkeypatch.setattr(settings, "media_dir", tmp_path)


class FakeHtmlRenderer:
    """Records the markup it was asked to screenshot."""

    def __init__(self) -> None:
        self.html: str | None = None

    def screenshot(self, html: str, width: int, height: int) -> bytes:
        self.html = html
        return b"PNG"


def a_logo(session, tmp_path_factory) -> Asset:
    """A real one-pixel asset on disk, so `data_uri` has bytes to embed."""
    from app.assets import assets_dir

    buffer = io.BytesIO()
    Image.new("RGB", (1, 1), "red").save(buffer, format="PNG")
    raw = buffer.getvalue()
    assets_dir().mkdir(parents=True, exist_ok=True)
    (assets_dir() / "logo.png").write_bytes(raw)
    asset = Asset(
        filename="logo.png", label="logo", kind=AssetKind.LOGO,
        width=1, height=1, sha256="deadbeef",
    )
    session.add(asset)
    session.flush()
    return asset


def test_preview_renders_a_slots_pinned_default(session, tmp_path_factory):
    """A logo slot the caller says nothing about renders from its pinned asset.

    This is the assertion that fails today: preview reads only what the caller sent, so
    the picture it shows is not the picture generation produces.
    """
    logo = a_logo(session, tmp_path_factory)
    template = create_template(
        session,
        kind=TemplateKind.VISUAL,
        name="pinned",
        body={"renderer": "html", "html": "<img src='{logo}'>"},
        slots=[{
            "name": "logo", "type": "image_url",
            "example": "https://example.invalid/x.png",
            "default_asset_id": logo.id,
        }],
    )
    session.commit()

    renderer = FakeHtmlRenderer()
    # `TestClient(app)` otherwise gets its own `get_session()` — a fresh connection that
    # cannot see this test's row without a real commit crossing connections. Overriding it
    # to the test's own session is what `client_with()` does in test_api_templates.py; the
    # brief's version of this test omitted it and 404s on a template that is, from the
    # route's fresh connection, invisible.
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_html_renderer] = lambda: renderer
    try:
        response = TestClient(app).post(f"/templates/{template.id}/preview", json={})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert "data:image/png;base64," in (renderer.html or "")
    assert "example.invalid" not in (renderer.html or "")


def test_preview_of_an_unsaved_body(session):
    """The editor previews what is in the textarea, which has no template id."""
    renderer = FakeHtmlRenderer()
    app.dependency_overrides[get_html_renderer] = lambda: renderer
    try:
        response = TestClient(app).post(
            "/templates/preview",
            json={
                "body": {"renderer": "html", "html": "<h1>{headline}</h1>"},
                "slots": [{"name": "headline", "type": "text"}],
                "values": {"headline": "Edited, not saved"},
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert "Edited, not saved" in (renderer.html or "")


def test_an_unsaved_ai_body_does_not_reach_the_image_service(session):
    """A textarea must not be able to spend money on a paid image generation."""
    called = False

    class ExplodingImageRenderer:
        def generate(self, prompt: str, width: int, height: int) -> bytes:
            nonlocal called
            called = True
            return b""

    app.dependency_overrides[get_image_renderer] = lambda: ExplodingImageRenderer()
    try:
        response = TestClient(app).post(
            "/templates/preview",
            json={"body": {"renderer": "ai", "prompt": "a picture"}, "slots": [], "values": {}},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 501
    assert called is False


def test_preview_of_an_unsaved_body_names_a_missing_slot(session):
    app.dependency_overrides[get_html_renderer] = lambda: FakeHtmlRenderer()
    try:
        response = TestClient(app).post(
            "/templates/preview",
            json={
                "body": {"renderer": "html", "html": "<h1>{headline}</h1>"},
                "slots": [{"name": "headline", "type": "text"}],
                "values": {},
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422
    assert "headline" in response.json()["detail"]
