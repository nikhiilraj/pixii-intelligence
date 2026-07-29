import httpx

from app.media import download_post_media
from app.models.post import Post


def post_with(url: str | None, *, thumbnail: str | None = None, zid: str = "a1") -> Post:
    return Post(
        zernio_id=zid,
        platform="linkedin",
        media_items=[{"type": "image", "url": url}] if url else [],
        thumbnail_url=thumbnail,
    )


def transport_serving(body: bytes = b"\x89PNG-bytes", status: int = 200) -> httpx.MockTransport:
    return httpx.MockTransport(lambda request: httpx.Response(status, content=body))


def test_downloads_media_and_stores_it_under_the_post_id(tmp_path):
    post = post_with("https://media.zernio.com/media/x_hero.png")

    stored = download_post_media(post, transport_serving(), tmp_path)

    assert stored == "a1.png"
    assert (tmp_path / "a1.png").read_bytes() == b"\x89PNG-bytes"


def test_falls_back_to_the_thumbnail_when_there_are_no_media_items(tmp_path):
    post = post_with(None, thumbnail="https://media.zernio.com/media/thumb.jpg")

    assert download_post_media(post, transport_serving(), tmp_path) == "a1.jpg"


def test_a_post_with_no_media_stores_nothing(tmp_path):
    post = post_with(None)

    assert download_post_media(post, transport_serving(), tmp_path) is None
    assert list(tmp_path.iterdir()) == []


def test_a_failed_download_returns_nothing_and_does_not_raise(tmp_path):
    """Media is supporting material — losing it must never cost us the post."""
    post = post_with("https://media.zernio.com/media/gone.png")

    assert download_post_media(post, transport_serving(status=404), tmp_path) is None
    assert list(tmp_path.iterdir()) == []


def test_a_network_error_returns_nothing_and_does_not_raise(tmp_path):
    def explode(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host")

    post = post_with("https://media.zernio.com/media/x.png")

    assert download_post_media(post, httpx.MockTransport(explode), tmp_path) is None


def test_already_downloaded_media_is_not_fetched_again(tmp_path):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url)
        return httpx.Response(200, content=b"first")

    post = post_with("https://media.zernio.com/media/x.png")
    transport = httpx.MockTransport(handler)

    download_post_media(post, transport, tmp_path)
    download_post_media(post, transport, tmp_path)

    assert len(calls) == 1
    assert (tmp_path / "a1.png").read_bytes() == b"first"


def test_a_url_without_a_recognisable_extension_still_stores(tmp_path):
    post = post_with("https://media.zernio.com/media/abc123")

    stored = download_post_media(post, transport_serving(), tmp_path)

    assert stored == "a1"
    assert (tmp_path / "a1").exists()


def typed(content_type: str) -> httpx.MockTransport:
    return httpx.MockTransport(
        lambda request: httpx.Response(200, content=b"x", headers={"content-type": content_type})
    )


def test_an_extensionless_url_takes_its_suffix_from_the_content_type(tmp_path):
    """`media.licdn.com` serves images from paths with no extension at all. Stored
    suffixless, StaticFiles hands them to the browser as octet-stream and they never
    render."""
    post = post_with("https://media.licdn.com/dms/image/v2/D4E10AQE6C1Hhn/0/1785247446818")

    assert download_post_media(post, typed("image/jpeg"), tmp_path) == "a1.jpg"


def test_a_content_type_that_names_no_file_type_still_stores(tmp_path):
    post = post_with("https://media.licdn.com/dms/image/v2/abc")

    assert download_post_media(post, typed("application/octet-stream"), tmp_path) == "a1"


def test_the_url_extension_wins_over_the_content_type(tmp_path):
    post = post_with("https://media.zernio.com/media/x_hero.png")

    assert download_post_media(post, typed("image/jpeg"), tmp_path) == "a1.png"


def test_a_content_type_suffixed_file_is_not_fetched_again(tmp_path):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url)
        return httpx.Response(200, content=b"x", headers={"content-type": "image/jpeg"})

    post = post_with("https://media.licdn.com/dms/image/v2/abc")
    transport = httpx.MockTransport(handler)

    download_post_media(post, transport, tmp_path)

    assert download_post_media(post, transport, tmp_path) == "a1.jpg"
    assert len(calls) == 1


def test_video_media_is_stored_with_its_own_extension(tmp_path):
    post = post_with("https://pixii-review.pages.dev/demos/title-bullets-demo.mp4")

    assert download_post_media(post, transport_serving(), tmp_path) == "a1.mp4"
