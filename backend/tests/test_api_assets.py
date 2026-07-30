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
from app.models.draft import Draft
from app.models.template import Template, TemplateKind, TemplateStatus


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


# --- DELETE /assets/{id} — the one irreversible operation in the library -------------------
#
# Every test here checks the file on disk as well as the row. The row is recoverable from a
# backup; the bytes are not, and "the row went away" is not the same claim as "the file did".


def visual_template(
    session: Session,
    *,
    slots: list[dict],
    version: int = 1,
    name: str = "stat-hero",
    status: TemplateStatus = TemplateStatus.APPROVED,
) -> Template:
    template = Template(
        family_id="stat-hero",
        version=version,
        kind=TemplateKind.VISUAL,
        name=name,
        status=status,
        body={"renderer": "html", "html": '<div><img src="{left_image_url}"></div>'},
        slots=slots,
    )
    session.add(template)
    session.flush()
    return template


def draft_using(session: Session, template: Template, values: dict[str, str]) -> Draft:
    draft = Draft(
        hook_family="h1",
        structure_family="s1",
        visual_family=template.family_id,
        visual_version=template.version,
        visual_values=values,
    )
    session.add(draft)
    session.flush()
    return draft


IMAGE_SLOT = [{"name": "left_image_url", "type": "image_url"}]


def test_deleting_an_asset_removes_the_row_and_the_file_from_disk(client, session):
    body = upload(client).json()
    path = assets_dir() / body["filename"]
    assert path.exists()

    response = client.delete(f"/assets/{body['id']}")

    assert response.status_code == 200
    assert response.json() == {"deleted": body["id"]}
    assert session.get(Asset, body["id"]) is None
    # The point of the confirmation dialog: the bytes are gone, not just the row.
    assert not path.exists()
    assert client.get("/assets").json() == []


def test_deleting_an_asset_that_is_not_in_the_library_is_a_404(client):
    response = client.delete("/assets/9999")

    assert response.status_code == 404
    assert "not in the library" in response.json()["detail"]


def test_a_draft_holding_the_asset_blocks_the_delete_and_is_named(client, session):
    body = upload(client).json()
    template = visual_template(session, slots=IMAGE_SLOT)
    draft = draft_using(session, template, {"left_image_url": str(body["id"])})

    response = client.delete(f"/assets/{body['id']}")

    assert response.status_code == 409
    detail = response.json()["detail"]
    # Named, not merely refused — "something references this" leaves the operator nowhere.
    assert f"draft {draft.id}" in detail
    assert "left_image_url" in detail
    # And nothing was half-done: the row and the file both survive a refusal.
    assert session.get(Asset, body["id"]) is not None
    assert (assets_dir() / body["filename"]).exists()


def test_a_template_slot_default_blocks_the_delete_and_is_named(client, session):
    body = upload(client).json()
    visual_template(
        session,
        name="stat-card",
        slots=[{"name": "logo", "type": "image_url", "default_asset_id": body["id"]}],
    )

    response = client.delete(f"/assets/{body['id']}")

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert "stat-card" in detail
    assert "logo" in detail
    assert (assets_dir() / body["filename"]).exists()


def test_a_default_asset_id_written_as_a_string_is_found_too(client, session):
    """`->> 'default_asset_id'` casts to text, so the JSON type of the id cannot hide it.

    Nothing writes this key yet (US-009 does), so both spellings are plausible and neither is
    the established one. A guard that only recognised the number would silently pass the
    string, and the whole point of the guard is that its failure mode is a deleted file.
    """
    body = upload(client).json()
    visual_template(
        session, slots=[{"name": "logo", "type": "image_url", "default_asset_id": str(body["id"])}]
    )

    assert client.delete(f"/assets/{body['id']}").status_code == 409


def test_a_text_slot_that_happens_to_hold_the_id_is_not_a_reference(client, session):
    """The discrimination the guard exists to make.

    `stat-hero`'s `big_number` slot holds prose the model wrote. A draft whose headline
    happens to read "7" is not holding asset 7, and blocking the delete would name a draft
    that does not reference it — a refusal the operator cannot act on, and a 409 nobody can
    ever clear. So the slot type decides, read off the template the draft actually used.
    """
    body = upload(client).json()
    template = visual_template(
        session,
        slots=[{"name": "big_number", "type": "text"}, *IMAGE_SLOT],
    )
    draft_using(session, template, {"big_number": str(body["id"])})

    response = client.delete(f"/assets/{body['id']}")

    assert response.status_code == 200
    assert not (assets_dir() / body["filename"]).exists()


def test_an_untyped_slot_holding_the_id_is_not_a_reference(client, session):
    """VISUAL row 556 carries four slots with no `type` key at all.

    `resolve_asset_values` skips a slot it cannot see a type on, so an id sitting in one was
    never resolved to this asset and deleting the file breaks nothing that was not already
    broken. Reading the type with `.get` rather than `slot["type"]` is what keeps this a
    decision instead of a `KeyError`.
    """
    body = upload(client).json()
    template = visual_template(session, slots=[{"name": "left_image_url"}])
    draft_using(session, template, {"left_image_url": str(body["id"])})

    assert client.delete(f"/assets/{body['id']}").status_code == 200


def test_a_draft_on_a_different_asset_does_not_block_the_delete(client, session):
    """Two assets, one referenced. The guard must be about this asset, not about any asset."""
    referenced = upload(client, image_bytes(colour="red")).json()
    spare = upload(client, image_bytes(colour="green")).json()
    template = visual_template(session, slots=IMAGE_SLOT)
    draft_using(session, template, {"left_image_url": str(referenced["id"])})

    assert client.delete(f"/assets/{spare['id']}").status_code == 200
    assert client.delete(f"/assets/{referenced['id']}").status_code == 409


def test_a_draft_that_used_an_earlier_version_still_holds_the_asset(client, session):
    """Lineage keys on `(family, version)`, so the slot types come off that exact version.

    v2 dropped the image slot; the draft was generated from v1, which had it. Looking the
    template up by family alone — or by newest version — would report the asset free while a
    real draft still points at it, and the file would go.
    """
    body = upload(client).json()
    v1 = visual_template(session, version=1, slots=IMAGE_SLOT)
    visual_template(session, version=2, slots=[{"name": "headline", "type": "text"}])
    draft_using(session, v1, {"left_image_url": str(body["id"])})

    assert client.delete(f"/assets/{body['id']}").status_code == 409
