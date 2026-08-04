import json

import httpx
import pytest

from app.models.draft import Draft
from app.models.template import TemplateKind
from app.publishing import PushFailed, lineage_metadata, push_draft
from app.templates import approve, create_template
from app.zernio import ZernioClient, ZernioRefused


def client_capturing(captured: dict, *, status: int = 201, body: dict | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        captured.setdefault("requests", []).append(
            {
                "path": request.url.path,
                "body": json.loads(request.content) if request.content else {},
                "request_id": request.headers.get("x-request-id"),
            }
        )
        return httpx.Response(
            status, json=body if body is not None else {"post": {"_id": "zpost-1"}}
        )

    return ZernioClient(
        api_key="k", base_url="https://example.test/api/v1", transport=httpx.MockTransport(handler)
    )


def a_draft(session) -> Draft:
    hook = create_template(session, kind=TemplateKind.HOOK, name="transformation", body={})
    structure = create_template(session, kind=TemplateKind.STRUCTURE, name="case-loop", body={})
    visual = create_template(session, kind=TemplateKind.VISUAL, name="stat-card", body={})
    for template in (hook, structure, visual):
        approve(session, template)

    draft = Draft(
        idea="an idea",
        mode="directed",
        hook_family=hook.family_id,
        hook_version=hook.version,
        structure_family=structure.family_id,
        structure_version=structure.version,
        visual_family=visual.family_id,
        visual_version=visual.version,
        hook_text="A hook line.",
        body_text="The body.",
    )
    session.add(draft)
    session.flush()
    return draft


def only_request(captured: dict) -> dict:
    return captured["requests"][0]


def test_the_post_reaches_zernio_as_a_draft(session):
    captured: dict = {}
    push_draft(session, a_draft(session), client_capturing(captured), account_id="acct-1")

    body = only_request(captured)["body"]
    assert body["isDraft"] is True
    assert "publishNow" not in body or body["publishNow"] is False
    assert "scheduledFor" not in body


def test_nothing_is_ever_scheduled_or_published(session):
    """Publishing is a human act. This service only ever creates drafts."""
    captured: dict = {}
    push_draft(session, a_draft(session), client_capturing(captured), account_id="acct-1")

    body = only_request(captured)["body"]
    assert not body.get("publishNow")
    assert not body.get("scheduledFor")
    assert not body.get("queuedFromProfile")


def test_the_full_text_is_sent_as_the_post_content(session):
    captured: dict = {}
    draft = a_draft(session)

    push_draft(session, draft, client_capturing(captured), account_id="acct-1")

    assert only_request(captured)["body"]["content"] == "A hook line.\n\nThe body."


def test_lineage_travels_in_the_zernio_metadata(session):
    captured: dict = {}
    draft = a_draft(session)

    push_draft(session, draft, client_capturing(captured), account_id="acct-1")

    metadata = only_request(captured)["body"]["metadata"]
    assert metadata["hook_family"] == draft.hook_family
    assert metadata["hook_version"] == draft.hook_version
    assert metadata["structure_family"] == draft.structure_family
    assert metadata["visual_family"] == draft.visual_family
    assert metadata["draft_id"] == draft.id
    assert metadata["mode"] == "directed"


def test_the_post_is_tagged_as_ours_so_it_is_findable_among_existing_drafts(session):
    captured: dict = {}
    push_draft(session, a_draft(session), client_capturing(captured), account_id="acct-1")

    body = only_request(captured)["body"]
    assert "pixii-intelligence" in body["tags"]
    assert body["metadata"]["source"] == "pixii-intelligence"


def test_the_returned_post_id_is_persisted_as_the_join_key(session):
    draft = a_draft(session)

    push_draft(session, draft, client_capturing({}), account_id="acct-1")

    assert draft.zernio_post_id == "zpost-1"
    assert draft.pushed_at is not None


def test_a_flat_response_shape_is_also_accepted(session):
    """Some responses return the post at the top level rather than under `post`."""
    draft = a_draft(session)

    push_draft(
        session, draft, client_capturing({}, body={"_id": "flat-1"}), account_id="acct-1"
    )

    assert draft.zernio_post_id == "flat-1"


def test_a_retry_reuses_the_request_id_so_no_second_post_is_created(session):
    captured: dict = {}
    draft = a_draft(session)
    client = client_capturing(captured)

    push_draft(session, draft, client, account_id="acct-1")
    push_draft(session, draft, client, account_id="acct-1", force=True)

    ids = [r["request_id"] for r in captured["requests"]]
    assert ids[0] == ids[1]
    assert ids[0]


def test_pushing_an_already_pushed_draft_does_not_call_zernio_again(session):
    captured: dict = {}
    draft = a_draft(session)
    client = client_capturing(captured)

    push_draft(session, draft, client, account_id="acct-1")
    push_draft(session, draft, client, account_id="acct-1")

    assert len(captured["requests"]) == 1


def test_a_rejection_is_surfaced_with_its_reason(session):
    draft = a_draft(session)
    client = client_capturing({}, status=422, body={"error": "content too long"})

    with pytest.raises(PushFailed) as raised:
        push_draft(session, draft, client, account_id="acct-1")

    assert "422" in str(raised.value)
    assert "content too long" in str(raised.value)
    assert draft.zernio_post_id is None


def test_a_response_without_a_post_id_is_a_failure_not_a_silent_success(session):
    draft = a_draft(session)

    with pytest.raises(PushFailed):
        push_draft(session, draft, client_capturing({}, body={"ok": True}), account_id="acct-1")

    assert draft.zernio_post_id is None


def test_the_target_account_is_named_in_the_platform_entry(session):
    captured: dict = {}
    push_draft(session, a_draft(session), client_capturing(captured), account_id="acct-1")

    platforms = only_request(captured)["body"]["platforms"]
    assert platforms == [{"platform": "linkedin", "accountId": "acct-1"}]


def test_lineage_metadata_is_flat_and_json_safe(session):
    """Zernio stores this verbatim, so it must survive a JSON round trip unchanged."""
    draft = a_draft(session)

    metadata = lineage_metadata(draft)

    assert json.loads(json.dumps(metadata)) == metadata
    assert all(isinstance(v, str | int) for v in metadata.values())


PUBLIC_URL = "https://media.zernio.test/temp/1700000000_abc_pixii-draft.png"


def client_with_media(captured: dict, *, presign_status: int = 200, put_status: int = 200):
    """A client that answers all three calls a push with a picture makes.

    Records them in one list so a test can assert on the order as well as the contents:
    the upload has to happen before the post is created, and that ordering is the whole
    reason a failed upload cannot leave a text-only post behind.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        captured.setdefault("requests", []).append(
            {
                "method": request.method,
                "path": request.url.path,
                "url": str(request.url),
                "body": json.loads(request.content)
                if request.content and request.method != "PUT"
                else {},
                "content": request.content if request.method == "PUT" else None,
                "request_id": request.headers.get("x-request-id"),
            }
        )
        if request.method == "PUT":
            return httpx.Response(put_status, text="denied" if put_status >= 400 else "")
        if request.url.path.endswith("/media/presign"):
            if presign_status >= 400:
                return httpx.Response(presign_status, json={"error": "file too large"})
            return httpx.Response(
                presign_status,
                json={"uploadUrl": "https://store.test/temp/x?sig=1", "publicUrl": PUBLIC_URL},
            )
        return httpx.Response(201, json={"post": {"_id": "zpost-1"}})

    return ZernioClient(
        api_key="k", base_url="https://example.test/api/v1", transport=httpx.MockTransport(handler)
    )


def a_draft_with_a_visual(session) -> Draft:
    draft = a_draft(session)
    draft.visual_image = b"\x89PNG\r\n\x1a\nrendered"
    session.add(draft)
    session.flush()
    return draft


def test_the_rendered_visual_is_uploaded_and_attached_to_the_post(session):
    captured: dict = {}

    push_draft(session, a_draft_with_a_visual(session), client_with_media(captured), account_id="a")

    presign, put, create = captured["requests"]
    assert presign["path"].endswith("/media/presign")
    assert presign["body"]["contentType"] == "image/png"
    assert put["method"] == "PUT"
    assert put["content"] == b"\x89PNG\r\n\x1a\nrendered"
    # The public URL from the presign response, not the signed upload URL.
    assert create["body"]["mediaItems"] == [{"url": PUBLIC_URL, "type": "image"}]


def test_the_post_still_reaches_zernio_as_a_draft_with_media_attached(session):
    """The safety property, asserted against the payload that now carries a picture.

    Media is another field on the same create call. If attaching it ever starts sending a
    schedule, a publish or a queue instruction, this is what says so.
    """
    captured: dict = {}

    push_draft(session, a_draft_with_a_visual(session), client_with_media(captured), account_id="a")

    create = captured["requests"][-1]["body"]
    assert create["mediaItems"]
    assert create["isDraft"] is True
    assert not create.get("publishNow")
    assert not create.get("scheduledFor")
    assert not create.get("queuedFromProfile")


def test_a_draft_with_no_visual_sends_no_media_key_at_all(session):
    """Not an empty list: `[]` claims the post has no picture, which is a different thing
    from a render that failed."""
    captured: dict = {}

    push_draft(session, a_draft(session), client_with_media(captured), account_id="a")

    requests = captured["requests"]
    assert len(requests) == 1  # nothing was uploaded
    assert "mediaItems" not in requests[0]["body"]


def test_the_image_is_uploaded_before_the_post_is_created(session):
    """The ordering is the guarantee: a failed upload cannot leave a text-only post."""
    captured: dict = {}

    push_draft(session, a_draft_with_a_visual(session), client_with_media(captured), account_id="a")

    methods = [(r["method"], r["path"].rsplit("/", 1)[-1]) for r in captured["requests"]]
    assert methods == [("POST", "presign"), ("PUT", "x"), ("POST", "posts")]


def test_a_failed_upload_creates_no_post_and_leaves_the_draft_retryable(session):
    captured: dict = {}
    draft = a_draft_with_a_visual(session)

    with pytest.raises(PushFailed) as raised:
        push_draft(session, draft, client_with_media(captured, presign_status=413), account_id="a")

    assert "file too large" in str(raised.value)
    assert [r["path"] for r in captured["requests"]] == ["/api/v1/media/presign"]
    assert draft.zernio_post_id is None
    assert draft.pushed_at is None


def test_a_store_that_rejects_the_upload_also_creates_no_post(session):
    captured: dict = {}
    draft = a_draft_with_a_visual(session)

    with pytest.raises(PushFailed):
        push_draft(session, draft, client_with_media(captured, put_status=403), account_id="a")

    assert not any(r["path"].endswith("/posts") for r in captured["requests"])
    assert draft.zernio_post_id is None


def test_pushing_a_pushed_draft_again_uploads_nothing_and_creates_nothing(session):
    """Idempotency has to survive the upload: a second push must not re-upload the picture
    any more than it re-creates the post."""
    captured: dict = {}
    draft = a_draft_with_a_visual(session)
    client = client_with_media(captured)

    push_draft(session, draft, client, account_id="a")
    push_draft(session, draft, client, account_id="a")

    assert len(captured["requests"]) == 3


def test_a_presign_that_answers_200_without_an_upload_target_is_a_push_failure(session):
    """HTTP 200 with the useful part missing is this API's signature failure, and it must
    reach the route as a failed push rather than an unhandled error."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"expiresIn": 3600})

    client = ZernioClient(
        api_key="k", base_url="https://example.test/api/v1", transport=httpx.MockTransport(handler)
    )
    draft = a_draft_with_a_visual(session)

    with pytest.raises(PushFailed):
        push_draft(session, draft, client, account_id="a")

    assert draft.zernio_post_id is None


def test_the_uploaded_url_is_persisted_on_the_draft(session):
    """Not a cache — the record that reproduces Zernio's duplicate hash on a retry."""
    draft = a_draft_with_a_visual(session)

    push_draft(session, draft, client_with_media({}), account_id="a")

    assert draft.zernio_media_url == PUBLIC_URL


def test_a_retry_after_a_lost_response_reuses_the_url_and_uploads_once(session):
    """The case the column exists for.

    Zernio accepts the post, the response never arrives, so `zernio_post_id` is never set
    and a human pushes again. The retry must send the *same* media URL: the duplicate hash
    covers `content + media URLs`, so a freshly presigned URL would hash differently and
    Zernio would accept it as a genuinely new post — in a colleague's account.
    """
    captured: dict = {}
    draft = a_draft_with_a_visual(session)

    class LosingTheResponse:
        """Uploads normally; the create call dies after Zernio has already accepted it."""

        def __init__(self, real):
            self._real = real

        def upload_media(self, *args, **kwargs):
            return self._real.upload_media(*args, **kwargs)

        def create_post(self, *args, **kwargs):
            raise httpx.ReadTimeout("response never arrived")

    real = client_with_media(captured)
    with pytest.raises(httpx.ReadTimeout):
        push_draft(session, draft, LosingTheResponse(real), account_id="a")

    # The URL survived the failed push — that is the whole point of committing it.
    assert draft.zernio_media_url == PUBLIC_URL

    push_draft(session, draft, real, account_id="a")

    presigns = [r for r in captured["requests"] if r["path"].endswith("/media/presign")]
    creates = [r for r in captured["requests"] if r["path"].endswith("/posts")]
    assert len(presigns) == 1  # the retry did not upload again
    assert len(creates) == 1
    assert creates[0]["body"]["mediaItems"] == [{"url": PUBLIC_URL, "type": "image"}]


def test_a_forced_repush_uploads_again_so_a_redrawn_picture_is_the_one_sent(session):
    """`force` exists to re-push a changed draft. Reusing the stored URL there would send
    the old image, silently — worse than the duplicate post that force already implies."""
    captured: dict = {}
    draft = a_draft_with_a_visual(session)
    client = client_with_media(captured)

    push_draft(session, draft, client, account_id="a")
    draft.visual_image = b"\x89PNG\r\n\x1a\nredrawn"
    push_draft(session, draft, client, account_id="a", force=True)

    puts = [r for r in captured["requests"] if r["method"] == "PUT"]
    assert [p["content"] for p in puts] == [
        b"\x89PNG\r\n\x1a\nrendered",
        b"\x89PNG\r\n\x1a\nredrawn",
    ]


def test_the_url_is_committed_before_the_post_is_created(session, monkeypatch):
    """A flush here would be rolled back with the request that dies mid-create — which is
    the one case the column exists for, so it would guard nothing while looking right.

    The rollback-per-test fixture cannot observe durability directly (see conftest), so
    this pins the ordering instead: a commit lands between the upload and the create.
    """
    order: list[str] = []
    draft = a_draft_with_a_visual(session)
    real_commit = session.commit

    def recording_commit():
        order.append("commit")
        real_commit()

    monkeypatch.setattr(session, "commit", recording_commit)

    class Recording:
        def __init__(self, real):
            self._real = real

        def upload_media(self, *args, **kwargs):
            order.append("upload")
            return self._real.upload_media(*args, **kwargs)

        def create_post(self, *args, **kwargs):
            order.append("create")
            return self._real.create_post(*args, **kwargs)

    push_draft(session, draft, Recording(client_with_media({})), account_id="a")

    assert order == ["upload", "commit", "create"]


def test_a_redraw_after_a_failed_push_sends_the_new_picture_not_the_stored_one(session):
    """The stale-image path, reachable with no `force` anywhere.

    A push whose create is refused still committed the upload URL. If the human then
    redraws and pushes again, reusing that URL would create the post carrying the picture
    they just replaced — silently, with the new one on screen. So a redraw clears it.
    """
    captured: dict = {}
    draft = a_draft_with_a_visual(session)

    refusing = client_with_media({})

    def refuse(*args, **kwargs):
        raise ZernioRefused("Zernio refused the post (422): content too long")

    refusing.create_post = refuse  # type: ignore[method-assign]
    with pytest.raises(PushFailed):
        push_draft(session, draft, refusing, account_id="a")
    assert draft.zernio_media_url == PUBLIC_URL  # the upload happened and was recorded

    # Stands in for a redraw: `generation._draw_visual` writes the new image and clears the
    # URL together, and that clearing is pinned in test_visual_iteration.py. What this test
    # covers is the other half — that a cleared URL makes the next push upload again.
    draft.visual_image = b"\x89PNG\r\n\x1a\nredrawn"
    draft.zernio_media_url = None
    session.add(draft)
    session.flush()

    push_draft(session, draft, client_with_media(captured), account_id="a")

    puts = [r for r in captured["requests"] if r["method"] == "PUT"]
    assert [p["content"] for p in puts] == [b"\x89PNG\r\n\x1a\nredrawn"]


def test_a_retry_inside_the_idempotency_window_adopts_the_original_post(session):
    """Zernio answers a repeated `x-request-id` with 200 and the post under `existingPost`.

    That is the idempotency guarantee working. Reading only `post` reported it as "accepted
    but returned no id" — untrue, and it left the draft looking unpushed while a real post
    sat in the account.
    """
    draft = a_draft_with_a_visual(session)
    client = client_with_media({}, )

    def existing(*args, **kwargs):
        return {"existingPost": {"_id": "zpost-original"}, "idempotent": True}

    client.create_post = existing  # type: ignore[method-assign]
    push_draft(session, draft, client, account_id="a")

    assert draft.zernio_post_id == "zpost-original"
    assert draft.pushed_at is not None
