import httpx
import pytest

from app.zernio import ZernioClient, ZernioResponseError


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


def test_sends_the_api_key_as_a_bearer_token():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json=page([], page_num=1, pages=0, total=0))

    client_returning(handler).fetch_posts()

    assert seen["auth"] == "Bearer test-key"
