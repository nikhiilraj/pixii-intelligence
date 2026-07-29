import httpx

from app.config import settings

# The API caps page size at 50. A larger value is not rejected — it returns HTTP 200
# with no posts and no pagination object, which reads exactly like an empty account.
PAGE_SIZE = 50


class ZernioResponseError(RuntimeError):
    """The API answered successfully but not with the shape a valid request produces."""


class ZernioRefused(RuntimeError):
    """The API declined to create the post. Carries the service's own reason."""


class ZernioClient:
    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._client = httpx.Client(
            base_url=base_url or settings.zernio_base_url,
            headers={"Authorization": f"Bearer {api_key or settings.zernio_api_key}"},
            timeout=30.0,
            transport=transport,
        )

    def fetch_posts(self, platform: str | None = None) -> list[dict]:
        """Every post the account can report on, paged.

        Deliberately sends no date range: a span beyond 90 days is another silently
        empty response, and the full history is what the corpus wants anyway.
        """
        posts: list[dict] = []
        page = 1
        while True:
            body = self._get_analytics_page(page, platform)
            posts.extend(body.get("posts") or [])
            pages = body["pagination"].get("pages", 1)
            if page >= pages:
                return posts
            page += 1

    def _get_analytics_page(self, page: int, platform: str | None) -> dict:
        params: dict[str, str | int] = {"limit": PAGE_SIZE, "page": page}
        if platform:
            params["platform"] = platform
        return self._get_page("/analytics", params)

    def _get_page(self, path: str, params: dict[str, str | int]) -> dict:
        response = self._client.get(path, params=params)
        response.raise_for_status()
        body = response.json()

        if "pagination" not in body:
            raise ZernioResponseError(
                f"{path} returned no pagination object — the request was rejected in a "
                f"way that looks like an empty account. Params: {params}"
            )
        return body

    def create_post(self, payload: dict, request_id: str | None = None) -> dict:
        """Create a post. Raises with the service's own reason if it declines.

        `x-request-id` makes the call idempotent, so a retry after a timeout cannot
        produce a second post on the account.
        """
        headers = {"x-request-id": request_id} if request_id else None
        response = self._client.post("/posts", json=payload, headers=headers)
        if response.status_code >= 400:
            raise ZernioRefused(
                f"Zernio refused the post ({response.status_code}): {response.text[:300]}"
            )
        return response.json()

    def list_posts(self) -> list[dict]:
        """Every post on the account, paged to completion.

        Unlike `/analytics` — a recent 50-row window — this is the account itself, and it
        carries `metadata`. It truncates two ways and neither looks like a failure: a
        `limit` above 50 answers HTTP 200 with nothing, and an unpaged read returns page 1
        of 4 that reads as a complete account. So the pages are walked and the collected
        count is checked against the total the API itself reported.
        """
        posts: list[dict] = []
        page, pages, total = 1, 1, None
        while True:
            body = self._get_page("/posts", {"limit": PAGE_SIZE, "page": page})
            posts.extend(body.get("posts") or [])
            pages = body["pagination"].get("pages", 1)
            total = body["pagination"].get("total")
            if page >= pages:
                break
            page += 1

        # A missing total is itself a failure: there would be nothing left to check the
        # collected count against, which is the one thing this method exists to do.
        if len(posts) != total:
            raise ZernioResponseError(
                f"/posts reported total={total} across {pages} pages but yielded "
                f"{len(posts)} — the response was truncated silently."
            )
        return posts

    def close(self) -> None:
        self._client.close()
