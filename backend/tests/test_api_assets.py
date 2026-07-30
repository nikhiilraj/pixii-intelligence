import io
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlmodel import Session, select

from app.assets import MAX_LONG_EDGE, assets_dir
from app.config import settings
from app.db import get_session
from app.main import app
from app.models.asset import Asset


@pytest.fixture
def client(session: Session, tmp_path, monkeypatch) -> Iterator[TestClient]:
    """A client whose uploads land in a temporary media directory.

    The `session` fixture rolls its transaction back, but nothing rolls back a file on
    disk. Pointing `settings.media_dir` at `tmp_path` is what keeps a test run from
    leaving rows-without-files behind for the next one to trip over.
    """
    monkeypatch.setattr(settings, "media_dir", tmp_path)
    app.dependency_overrides[get_session] = lambda: session
    yield TestClient(app)
    app.dependency_overrides.clear()


def image_bytes(width: int = 40, height: int = 40, fmt: str = "PNG", colour: str = "red") -> bytes:
    """An image built in memory. No test here may reach the network for a file."""
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), colour).save(buffer, format=fmt)
    return buffer.getvalue()


def upload(
    client: TestClient,
    raw: bytes = b"",
    *,
    name: str = "wordmark.png",
    content_type: str = "image/png",
    kind: str = "logo",
    **data: object,
):
    return client.post(
        "/assets",
        files={"file": (name, raw or image_bytes(), content_type)},
        data={"kind": kind, **data},
    )


def test_an_upload_is_stored_and_recorded(client):
    response = upload(client, image_bytes(64, 48), label="Pixii wordmark", tags=["brand", "orange"])

    assert response.status_code == 200
    body = response.json()
    assert (body["width"], body["height"]) == (64, 48)
    assert body["kind"] == "logo"
    assert body["label"] == "Pixii wordmark"
    assert body["tags"] == ["brand", "orange"]
    assert body["source_post_id"] is None
    # Named for its own digest, under the directory the /media mount already serves.
    assert body["filename"] == f"{body['sha256']}.png"
    assert (assets_dir() / body["filename"]).exists()


def test_the_label_falls_back_to_the_uploaded_file_stem(client):
    body = upload(client, name="ghost-figure.png").json()

    assert body["label"] == "ghost-figure"


def test_re_uploading_an_identical_file_returns_the_existing_row(client, session):
    raw = image_bytes(70, 70, colour="blue")

    first = upload(client, raw, label="first").json()
    second = upload(client, raw, name="a-copy.png", label="second", kind="product").json()

    # The same row, not a second one carrying the second upload's label.
    assert second["id"] == first["id"]
    assert second["label"] == "first"
    assert second["kind"] == "logo"
    assert len(session.exec(select(Asset)).all()) == 1
    assert len(list(assets_dir().iterdir())) == 1


def test_an_oversized_image_is_stored_downscaled_with_its_real_dimensions(client):
    body = upload(client, image_bytes(MAX_LONG_EDGE * 2, MAX_LONG_EDGE)).json()

    assert (body["width"], body["height"]) == (MAX_LONG_EDGE, MAX_LONG_EDGE // 2)
    # The recorded size describes the bytes on disk, not what was uploaded — a renderer
    # embedding this file gets exactly the dimensions the row claims.
    with Image.open(assets_dir() / body["filename"]) as stored:
        assert stored.size == (body["width"], body["height"])


def test_an_image_at_the_ceiling_is_stored_untouched(client):
    raw = image_bytes(MAX_LONG_EDGE, 400)

    body = upload(client, raw).json()

    assert (body["width"], body["height"]) == (MAX_LONG_EDGE, 400)
    # Not re-encoded: an image that needs no resizing keeps the bytes it arrived with.
    assert (assets_dir() / body["filename"]).read_bytes() == raw


def test_a_non_image_upload_is_refused_and_stores_nothing(client, session):
    response = upload(client, b"this is not an image at all", name="notes.png")

    assert response.status_code == 422
    assert "not a readable image" in response.json()["detail"]
    assert session.exec(select(Asset)).all() == []
    assert not assets_dir().exists()


def test_a_truncated_image_is_refused_rather_than_half_stored(client, session):
    truncated = image_bytes(200, 200)[:120]

    assert upload(client, truncated).status_code == 422
    assert session.exec(select(Asset)).all() == []


def test_an_image_format_the_media_mount_cannot_serve_is_refused(client):
    """A stored `.tiff` would be served as octet-stream and render nowhere."""
    response = upload(client, image_bytes(fmt="TIFF"), name="scan.tiff", content_type="image/tiff")

    assert response.status_code == 422
    assert "not a format this library serves" in response.json()["detail"]


def test_an_upload_with_no_extension_still_gets_one_from_its_content_type(client):
    """The `media.licdn.com` case: a name that says nothing about the file type."""
    body = upload(client, name="wordmark", content_type="image/png").json()

    assert body["filename"].endswith(".png")


def test_a_name_that_lies_about_the_format_does_not_decide_the_suffix(client):
    body = upload(client, image_bytes(fmt="PNG"), name="wordmark.gif").json()

    assert body["filename"].endswith(".png")


def test_a_jpeg_keeps_the_extension_it_was_uploaded_under(client):
    raw = image_bytes(fmt="JPEG")

    body = upload(client, raw, name="shot.jpeg", content_type="image/jpeg").json()

    assert body["filename"].endswith(".jpeg")


def test_lists_assets_newest_first(client):
    upload(client, image_bytes(colour="red"), label="older")
    upload(client, image_bytes(colour="green"), label="newer")

    body = client.get("/assets").json()

    assert [a["label"] for a in body] == ["newer", "older"]


def test_filters_by_kind(client):
    upload(client, image_bytes(colour="red"), label="a logo", kind="logo")
    upload(client, image_bytes(colour="green"), label="a product", kind="product")

    body = client.get("/assets", params={"kind": "product"}).json()

    assert [a["label"] for a in body] == ["a product"]


def test_filters_by_tag(client):
    upload(client, image_bytes(colour="red"), label="tagged", tags=["cream", "hero"])
    upload(client, image_bytes(colour="green"), label="untagged")
    upload(client, image_bytes(colour="blue"), label="other", tags=["hero"])

    body = client.get("/assets", params={"tag": "cream"}).json()

    assert [a["label"] for a in body] == ["tagged"]
    assert [a["label"] for a in client.get("/assets", params={"tag": "hero"}).json()] == [
        "other",
        "tagged",
    ]


def test_a_row_inserted_without_tags_is_still_listed(client, session):
    """A guard for the slice that promotes corpus media by building rows directly.

    `tags` is a nullable JSONB column, and `tags @> '["x"]'` on NULL is NULL — a row with
    no tags at all would drop out of every tag query and out of `GET /assets` with it. The
    model's `default_factory` is what stores `[]` instead, so an untagged row is merely
    untagged rather than invisible.
    """
    session.add(
        Asset(filename="promoted.png", kind="product", width=8, height=8, sha256="promoted-digest")
    )
    session.flush()

    body = client.get("/assets").json()

    assert [a["label"] for a in body] == [""]
    assert body[0]["tags"] == []
    assert client.get("/assets", params={"tag": "hero"}).json() == []


def test_an_unknown_kind_is_rejected_with_the_allowed_values(client):
    response = client.get("/assets", params={"kind": "mascot"})

    assert response.status_code == 422
    detail = response.json()["detail"][0]
    assert detail["loc"] == ["query", "kind"]
    # The caller is told what it could have said, rather than being handed an empty list.
    assert "'logo'" in detail["msg"]
    assert "'photo'" in detail["msg"]


def test_an_unknown_kind_on_upload_is_rejected_too(client):
    assert upload(client, kind="mascot").status_code == 422
