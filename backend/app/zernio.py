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
        # A second client for one job: the PUT to a presigned storage URL in `upload_media`.
        # It cannot be `self._client`, which carries a `base_url` and an `Authorization`
        # header. The presigned URL is absolute and already signed, and the signature covers
        # a fixed set of headers — an unexpected `Authorization` is what the signature was
        # computed without, so the store rejects the request. Same transport so a test can
        # intercept both halves of the upload.
        self._uploads = httpx.Client(timeout=60.0, transport=transport)

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

    def update_post(self, post_id: str, payload: dict, request_id: str | None = None) -> dict:
        """Change a post that already exists. This is how a draft stops being a draft.

        **`isDraft: false` is what takes it out of draft state, not `scheduledFor`.** Sending
        a time alone leaves an existing draft sitting in draft — it acquires a schedule it
        will never act on, and the post looks scheduled in Pixii and is not in Zernio. The
        docs are explicit about it and it is the single easiest way to ship a publishing
        feature that silently does nothing.

        `x-request-id` for the same reason `create_post` takes one: a retry after a timeout
        must be the same logical command, not a second one.

        A refusal raises with the service's own words. A validation refusal and a network
        timeout are different events and only one of them may be retried, which is why this
        does not swallow either into a bare False.
        """
        headers = {"x-request-id": request_id} if request_id else None
        response = self._client.put(f"/posts/{post_id}", json=payload, headers=headers)
        if response.status_code >= 400:
            raise ZernioRefused(
                f"Zernio refused the update ({response.status_code}): {response.text[:300]}"
            )
        return dict(response.json())

    def get_post(self, post_id: str) -> dict:
        """One post as Zernio currently sees it. The reconciler's only question.

        Not `list_posts`: that walks every page of the account to answer "what exists",
        where this answers "what happened to this one".
        """
        response = self._client.get(f"/posts/{post_id}")
        if response.status_code >= 400:
            raise ZernioRefused(
                f"Zernio would not return post {post_id} ({response.status_code}): "
                f"{response.text[:300]}"
            )
        body = response.json()
        # The single-post route returns the post either bare or wrapped in `post`; both
        # shapes are documented across versions and neither is an error.
        post = body.get("post") if isinstance(body, dict) else None
        return dict(post if isinstance(post, dict) else body)

    def upload_media(self, data: bytes, filename: str, content_type: str) -> str:
        """Put these bytes where a post can reference them. Returns the public URL.

        Media reaches a post **by URL only**: `mediaItems[].url` is the sole way in, and the
        schema requires it be "publicly reachable over HTTPS". There is no multipart upload
        on `/posts` and no base64 field, so a picture we hold as bytes has to become a URL
        first — two calls, presign then PUT, before the post can be created at all.

        `/media/upload-direct` looks like the same thing in one call and is not: it is the
        inbox-attachment endpoint, capped at 25 MB, and its files auto-delete after seven
        days with no path to permanent storage. Presigned uploads are copied to permanent
        storage when a post using them publishes. For a picture meant to outlive the push,
        the one-call route is the wrong one.

        ponytail: the returned URL is not stored, so a re-push (`force=True`) uploads again
        and gets a new URL. That is what makes a re-push after a redraw carry the new image
        rather than the old one — but it also means the retry of an ambiguous create is no
        longer caught by the API's own 24-hour duplicate hash, which is keyed on content
        **plus media URLs**. Storing the URL against a hash of the image bytes fixes both;
        it costs a column and a migration, and nothing has needed it yet.
        """
        response = self._client.post(
            "/media/presign",
            # `size` is optional and pre-validates against the 5 GB ceiling. Sent because a
            # rejection here costs one round trip, where a rejection at PUT time costs the
            # upload itself.
            json={"filename": filename, "contentType": content_type, "size": len(data)},
        )
        if response.status_code >= 400:
            raise ZernioRefused(
                f"Zernio refused the upload ({response.status_code}): {response.text[:300]}"
            )

        body = response.json()
        upload_url, public_url = body.get("uploadUrl"), body.get("publicUrl")
        if not upload_url or not public_url:
            raise ZernioResponseError(
                f"/media/presign returned no upload target: {str(body)[:200]}"
            )

        # The Content-Type must be the one presign was asked for, byte for byte: it is part
        # of what the URL was signed over, so a mismatch fails the signature rather than the
        # upload, and the error comes back from the storage host in its own vocabulary.
        stored = self._uploads.put(
            upload_url, content=data, headers={"Content-Type": content_type}
        )
        if stored.status_code >= 400:
            raise ZernioRefused(
                f"The media store refused the upload ({stored.status_code}): "
                f"{stored.text[:300]}"
            )
        return str(public_url)

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
        self._uploads.close()
