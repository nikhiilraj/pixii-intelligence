import json

import httpx
import pytest

from app.zernio import ZernioClient, ZernioRefused, ZernioResponseError


def client_returning(handler) -> ZernioClient:
    """A ZernioClient wired to a fake transport, so no test touches the network."""
    return ZernioClient(
        api_key="test-key",
        base_url="https://example.test/api/v1",
        transport=httpx.MockTransport(handler),
    )


def page(posts: list[dict], *, page_num: int, pages: int, total: int) -> dict:
    return {
        "posts": posts,
        "pagination": {"page": page_num, "limit": 50, "total": total, "pages": pages},
    }


def post(zid: str) -> dict:
    return {"_id": zid, "platform": "linkedin", "content": f"post {zid}", "analytics": {}}


def test_fetches_every_page_and_returns_all_posts():
    requested_pages = []

    def handler(request: httpx.Request) -> httpx.Response:
        page_num = int(request.url.params["page"])
        requested_pages.append(page_num)
        posts = [post(f"p{page_num}-{i}") for i in range(50 if page_num < 3 else 7)]
        return httpx.Response(200, json=page(posts, page_num=page_num, pages=3, total=107))

    posts = client_returning(handler).fetch_posts()

    assert requested_pages == [1, 2, 3]
    assert len(posts) == 107


def test_always_requests_the_maximum_supported_page_size():
    """limit above 50 makes the API silently return nothing, so it must never be exceeded."""
    seen_limits = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_limits.append(request.url.params["limit"])
        return httpx.Response(200, json=page([post("a")], page_num=1, pages=1, total=1))

    client_returning(handler).fetch_posts()

    assert seen_limits == ["50"]


def test_a_response_without_pagination_is_an_error_not_an_empty_account():
    """The API answers an unsupported request with HTTP 200 and no posts.

    Treating that as "no posts" is how a wrong assumption survives undetected, so the
    missing pagination object must surface as a failure.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"posts": []})

    with pytest.raises(ZernioResponseError):
        client_returning(handler).fetch_posts()


def test_a_genuinely_empty_account_returns_no_posts():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=page([], page_num=1, pages=0, total=0))

    assert client_returning(handler).fetch_posts() == []


def test_an_http_error_is_raised_rather_than_swallowed():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "unauthorized"})

    with pytest.raises(httpx.HTTPStatusError):
        client_returning(handler).fetch_posts()


def test_list_posts_pages_the_whole_account_not_just_the_first_page():
    """An unpaged read returns page 1 of 4 and looks exactly like a complete account."""
    requested = []

    def handler(request: httpx.Request) -> httpx.Response:
        page_num = int(request.url.params["page"])
        requested.append((page_num, request.url.params["limit"]))
        posts = [post(f"p{page_num}-{i}") for i in range(50 if page_num < 4 else 5)]
        return httpx.Response(200, json=page(posts, page_num=page_num, pages=4, total=155))

    posts = client_returning(handler).list_posts()

    assert requested == [(1, "50"), (2, "50"), (3, "50"), (4, "50")]
    assert len(posts) == 155


def test_list_posts_fails_when_fewer_posts_arrive_than_the_total_promises():
    """Silent truncation is this API's signature failure, so the count is checked."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=page([post("a")], page_num=1, pages=1, total=155))

    with pytest.raises(ZernioResponseError):
        client_returning(handler).list_posts()


def test_list_posts_fails_when_the_api_reports_no_total_to_check_against():
    """Without a total there is nothing to verify the count against, and this API's whole
    pathology is responses that look complete and are not."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"posts": [post("a")], "pagination": {"page": 1, "pages": 1}}
        )

    with pytest.raises(ZernioResponseError):
        client_returning(handler).list_posts()


def test_list_posts_treats_a_missing_pagination_object_as_a_failure():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"posts": []})

    with pytest.raises(ZernioResponseError):
        client_returning(handler).list_posts()


def test_sends_the_api_key_as_a_bearer_token():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json=page([], page_num=1, pages=0, total=0))

    client_returning(handler).fetch_posts()

    assert seen["auth"] == "Bearer test-key"


PRESIGNED = "https://store.test/temp/1700000000_abc_pixii.png?X-Amz-Signature=sig"
PUBLIC = "https://media.zernio.test/temp/1700000000_abc_pixii.png"


def upload_handler(requests: list, *, presign_status: int = 200, put_status: int = 200):
    """Answers both halves of an upload: the presign call and the PUT to the store."""

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "PUT":
            return httpx.Response(put_status, text="" if put_status < 400 else "denied")
        if presign_status >= 400:
            return httpx.Response(presign_status, json={"error": "unsupported contentType"})
        return httpx.Response(
            presign_status,
            json={
                "uploadUrl": PRESIGNED,
                "publicUrl": PUBLIC,
                "key": "temp/1700000000_abc_pixii.png",
                "expiresIn": 3600,
            },
        )

    return handler


def test_upload_media_presigns_then_puts_the_bytes_and_returns_the_public_url():
    requests: list[httpx.Request] = []

    url = client_returning(upload_handler(requests)).upload_media(b"PNGDATA", "v.png", "image/png")

    presign, put = requests
    assert presign.method == "POST"
    assert presign.url.path == "/api/v1/media/presign"
    assert json.loads(presign.content) == {
        "filename": "v.png",
        "contentType": "image/png",
        "size": 7,
    }
    assert put.method == "PUT"
    assert str(put.url) == PRESIGNED
    assert put.content == b"PNGDATA"
    # The public URL, not the signed one: the signed URL expires in an hour and is a
    # credential, and it is the public one a post references.
    assert url == PUBLIC


def test_the_upload_put_carries_no_authorization_header():
    """A presigned URL is signed over a fixed set of headers. An unexpected `Authorization`
    is not among them, and the store rejects the request rather than ignoring it."""
    requests: list[httpx.Request] = []

    client_returning(upload_handler(requests)).upload_media(b"x", "v.png", "image/png")

    put = requests[1]
    assert "authorization" not in {k.lower() for k in put.headers}
    assert put.headers["content-type"] == "image/png"


def test_a_refused_presign_carries_the_services_own_reason():
    requests: list[httpx.Request] = []
    client = client_returning(upload_handler(requests, presign_status=400))

    with pytest.raises(ZernioRefused) as raised:
        client.upload_media(b"x", "v.png", "image/png")

    assert "400" in str(raised.value)
    assert "unsupported contentType" in str(raised.value)
    assert len(requests) == 1  # nothing was uploaded


def test_a_store_that_rejects_the_put_is_a_failure_not_a_silent_success():
    requests: list[httpx.Request] = []
    client = client_returning(upload_handler(requests, put_status=403))

    with pytest.raises(ZernioRefused) as raised:
        client.upload_media(b"x", "v.png", "image/png")

    assert "403" in str(raised.value)


def test_a_presign_without_an_upload_target_is_an_error_not_an_empty_url():
    """This API's signature failure is HTTP 200 with the useful part missing."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"expiresIn": 3600})

    with pytest.raises(ZernioResponseError):
        client_returning(handler).upload_media(b"x", "v.png", "image/png")
