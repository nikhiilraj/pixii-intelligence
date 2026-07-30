"""Corpus media promoted into the asset library, so a picker has real brand material.

Nothing here reaches the network. Media is built in memory with Pillow and written where a
`Post.local_media_path` points, which is exactly the shape `media.download_post_media`
leaves behind: a flat `media/<zernio_id><suffix>`.
"""

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
from app.models.post import Post


@pytest.fixture
def client(session: Session, tmp_path, monkeypatch) -> Iterator[TestClient]:
    """A client whose media directory — source *and* destination — is temporary.

    Promotion reads from `settings.media_dir` and writes to `media_dir/assets`, so one
    redirect covers both. The `session` fixture rolls its transaction back; nothing rolls
    back a file, which is why this matters.
    """
    monkeypatch.setattr(settings, "media_dir", tmp_path)
    app.dependency_overrides[get_session] = lambda: session
    yield TestClient(app)
    app.dependency_overrides.clear()


def image_bytes(width: int = 40, height: int = 40, fmt: str = "PNG", colour: str = "red") -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), colour).save(buffer, format=fmt)
    return buffer.getvalue()


def post_with_media(
    session: Session,
    raw: bytes | None = None,
    *,
    zid: str = "p1",
    suffix: str = ".png",
    records_missing_file: bool = False,
) -> Post:
    """A corpus post, with its media on disk where the real downloader would have put it."""
    post = Post(zernio_id=zid, platform="linkedin")
    if raw is not None or records_missing_file:
        post.local_media_path = f"{zid}{suffix}"
        if raw is not None:
            (settings.media_dir / post.local_media_path).write_bytes(raw)
    session.add(post)
    session.flush()
    return post


def promote(client: TestClient, *post_ids: int | None, kind: str = "photo", **body: object):
    return client.post(
        "/assets/promote", json={"post_ids": list(post_ids), "kind": kind, **body}
    )


def test_promoting_a_post_creates_an_asset_pointing_back_at_it(client, session):
    post = post_with_media(session, image_bytes(64, 48))

    response = promote(client, post.id, kind="product", tags=["corpus", "hero"])

    assert response.status_code == 200
    (body,) = response.json()
    # The column US-005 added, populated for the first time: this asset has a provenance.
    assert body["source_post_id"] == post.id
    assert (body["width"], body["height"]) == (64, 48)
    assert body["kind"] == "product"
    assert body["tags"] == ["corpus", "hero"]
    # Stored on the upload path, so named for its own digest under the served directory.
    assert body["filename"] == f"{body['sha256']}.png"
    assert (assets_dir() / body["filename"]).exists()
    # The post's own file is untouched — promotion copies, it does not move.
    assert (settings.media_dir / str(post.local_media_path)).exists()


def test_a_promoted_asset_is_offered_by_the_library(client, session):
    """The point of the slice: the picker's list stops being empty."""
    post = post_with_media(session, image_bytes())

    promote(client, post.id)

    listed = client.get("/assets").json()
    assert [a["source_post_id"] for a in listed] == [post.id]
    assert client.get("/assets", params={"kind": "photo"}).json() == listed


def test_the_label_falls_back_to_the_media_filename_stem(client, session):
    post = post_with_media(session, image_bytes(), zid="6a22bc122b2567671adfa69b")

    (body,) = promote(client, post.id).json()

    assert body["label"] == "6a22bc122b2567671adfa69b"


def test_promoting_the_same_post_twice_does_not_duplicate(client, session):
    post = post_with_media(session, image_bytes(70, 70, colour="blue"))

    first = promote(client, post.id).json()
    second = promote(client, post.id, kind="logo", tags=["again"]).json()

    # The same row, not a second one carrying the second call's kind.
    assert second[0]["id"] == first[0]["id"]
    assert second[0]["kind"] == "photo"
    assert len(session.exec(select(Asset)).all()) == 1
    assert len(list(assets_dir().iterdir())) == 1


def test_two_posts_sharing_identical_media_promote_to_one_asset(client, session):
    """Dedupe is on `sha256`, and several corpus posts can carry the same picture.

    Both ids in one request is the case a naive loop gets wrong: without the flush inside
    the loop, the second post's dedupe query cannot see the row the first one added and both
    insert, colliding on the unique `sha256`.
    """
    raw = image_bytes(30, 30, colour="green")
    first_post = post_with_media(session, raw, zid="p1")
    second_post = post_with_media(session, raw, zid="p2")

    body = promote(client, first_post.id, second_post.id).json()

    assert [a["id"] for a in body] == [body[0]["id"], body[0]["id"]]
    # One file, one row, and the provenance of whichever post got there first.
    assert body[0]["source_post_id"] == first_post.id
    assert len(session.exec(select(Asset)).all()) == 1
    assert len(list(assets_dir().iterdir())) == 1


def test_naming_the_same_post_twice_in_one_request_is_one_promotion(client, session):
    post = post_with_media(session, image_bytes())

    body = promote(client, post.id, post.id).json()

    assert len(body) == 1
    assert len(session.exec(select(Asset)).all()) == 1


def test_a_post_with_no_media_is_refused_and_promotes_nothing(client, session):
    """`local_media_path` is None for many rows — download was opportunistic."""
    good = post_with_media(session, image_bytes(), zid="p1")
    empty = post_with_media(session, None, zid="p2")

    response = promote(client, good.id, empty.id)

    assert response.status_code == 422
    assert response.json()["detail"] == f"post {empty.id} has no downloaded media to promote"
    # All-or-nothing: the good post in the same request was not half-promoted either.
    assert session.exec(select(Asset)).all() == []
    assert not assets_dir().exists()


def test_a_post_that_is_not_in_the_corpus_is_a_404(client):
    response = promote(client, 9999)

    assert response.status_code == 404
    assert "not in the corpus" in response.json()["detail"]


def test_a_recorded_media_file_that_is_gone_says_so_rather_than_succeeding_quietly(
    client, session
):
    """The one place `media.py`'s swallow-and-return-None would have been wrong.

    A post can carry a path whose file no longer exists. Promotion asked for that exact
    file, so a silent success would leave the operator with a library missing what they
    picked — and, worse, an asset row with no bytes behind it.
    """
    post = post_with_media(session, None, records_missing_file=True)

    response = promote(client, post.id)

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "p1.png" in detail
    assert "cannot be read" in detail
    assert session.exec(select(Asset)).all() == []


def test_a_post_whose_media_is_a_video_is_refused(client, session):
    """Real, not hypothetical: 3 of the 62 files in `media/` are `.mp4`."""
    post = post_with_media(session, b"\x00\x00\x00 ftypisom-not-an-image", suffix=".mp4")

    response = promote(client, post.id)

    assert response.status_code == 422
    assert "not a readable image" in response.json()["detail"]
    assert session.exec(select(Asset)).all() == []


def test_a_jpeg_post_image_is_promoted_under_its_own_suffix(client, session):
    """Corpus media is `.jpg` more often than `.png`, so the format cannot be assumed."""
    post = post_with_media(session, image_bytes(fmt="JPEG"), suffix=".jpg")

    (body,) = promote(client, post.id).json()

    assert body["filename"].endswith(".jpg")
    assert (assets_dir() / body["filename"]).exists()


def test_an_oversized_post_image_is_downscaled_exactly_as_an_upload_is(client, session):
    """Promotion goes through `store_image`, so it inherits the long-edge ceiling."""
    post = post_with_media(session, image_bytes(MAX_LONG_EDGE * 2, MAX_LONG_EDGE))

    (body,) = promote(client, post.id).json()

    assert (body["width"], body["height"]) == (MAX_LONG_EDGE, MAX_LONG_EDGE // 2)
    with Image.open(assets_dir() / body["filename"]) as stored:
        assert stored.size == (body["width"], body["height"])


def test_an_unknown_kind_is_rejected(client, session):
    post = post_with_media(session, image_bytes())

    assert promote(client, post.id, kind="mascot").status_code == 422
    assert session.exec(select(Asset)).all() == []
