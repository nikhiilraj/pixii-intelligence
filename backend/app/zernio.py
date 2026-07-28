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

        response = self._client.get("/analytics", params=params)
        response.raise_for_status()
        body = response.json()

        if "pagination" not in body:
            raise ZernioResponseError(
                f"analytics page {page} returned no pagination object — the request was "
                f"rejected in a way that looks like an empty account. Params: {params}"
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

    def list_posts(self, limit: int = PAGE_SIZE, page: int = 1) -> list[dict]:
        """Posts as Zernio holds them. Unlike /analytics, this carries `metadata`."""
        response = self._client.get("/posts", params={"limit": limit, "page": page})
        response.raise_for_status()
        body = response.json()
        return body.get("posts") or []

    def close(self) -> None:
        self._client.close()
