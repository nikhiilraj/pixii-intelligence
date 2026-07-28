import json

import httpx
import pytest

from app.models.draft import Draft
from app.models.template import TemplateKind
from app.publishing import PushFailed, lineage_metadata, push_draft
from app.templates import approve, create_template
from app.zernio import ZernioClient


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
