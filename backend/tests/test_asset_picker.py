"""The picked-asset path, end to end: a visual with image slots that actually completes.

Before this slice no draft in Postgres had ever rendered a visual carrying an image — the
model may not write an `image_url` slot (`WRITABLE_SLOT_TYPES`) and nothing else filled one,
so `stat-hero` v2 could only ever produce `MissingSlotValue`. Everything here is that path
running for the first time.

Every renderer is `renderer_capturing` — a real `CloudflareRenderer` over an
`httpx.MockTransport` that keeps the posted body. `FakeRenderer` (`test_generation.py`)
discards the `html` argument, so under it a slice that embedded nothing would pass. Nothing
here touches the network or the real media directory.
"""

import io
import json
from collections.abc import Iterator

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlmodel import Session

from app.assets import (
    AssetInUse,
    data_uri,
    delete_asset,
    holders_of,
    sha256_of,
    store_image,
)
from app.autonomous import run_autonomous
from app.config import settings
from app.db import get_session
from app.deps import get_html_renderer, get_llm
from app.generation import (
    WRITABLE_SLOT_TYPES,
    chosen_assets,
    generate_draft,
    regenerate_text,
    regenerate_visual,
)
from app.main import app
from app.models.asset import Asset, AssetKind
from app.models.draft import Draft
from app.models.post import Post
from app.models.template import Template, TemplateKind
from app.publishing import lineage_metadata, push_draft
from app.templates import approve, create_template
from app.zernio import ZernioClient
from tests.test_autonomous import FakeImageRenderer
from tests.test_generation import WorkflowLLM
from tests.test_renderer_selection import NoSearch
from tests.test_rendering import renderer_capturing

# `stat-hero` v2 (template 1101) as it really is: two text slots and two `image_url` slots.
STAT_HERO = (
    "<div><span>{big_number}</span><h1>{headline}</h1>"
    '<img src="{left_image_url}"><img src="{right_image_url}"></div>'
)

WRITTEN = {
    "hook": "A 9-figure exit.",
    # Long enough to clear `gates.POST_MIN_CHARS`. `run_autonomous` runs the reviewed
    # workflow now, so a one-sentence body stops at `failed_review` before a renderer is
    # ever reached — and this file is about what fills the image slots, not about gates.
    "body": (
        "One main image did it. Not a rebrand and not a bigger ad budget: the listing "
        "was redrawn once and the rest followed from there, which is the cheapest change "
        "nobody had looked at."
    ),
    # The model volunteers the image slots too, exactly as it does in production. They are
    # dropped by `_written_values`, and this file exists because something else must fill them.
    "visual_values": {
        "big_number": "$325M",
        "headline": "One image. Every month.",
        "left_image_url": "a product photo",
        "right_image_url": "the Pixii logo",
    },
}


class FakeLLM:
    """One canned answer for the write call, and the same dict for anything else.

    `run_autonomous` also asks it to propose topics, and a dict carrying both `topics` and the
    written post satisfies both readers — the same shape `test_autonomous.FakeLLM` relies on.
    """

    def __init__(self, written: dict | None = None):
        self.written = written or WRITTEN

    def complete_json(self, system: str, user: str, images=()) -> dict:
        # The planning and review answers first. `run_autonomous` is on the reviewed path now,
        # so a stub answering everything with the written post hands a *post* to the brief
        # step; the workflow records that as a planning failure and returns rather than
        # raising, and every assertion below would quietly become an assertion about a
        # `failed` row. Borrowed from `test_generation.WorkflowLLM` rather than restated, so
        # there is one description of what a passing workflow says.
        for marker, answer in WorkflowLLM.PLANNING.items():
            if system.lower().startswith(marker):
                return dict(answer)
        if "choose which templates" in system.lower():
            # `reason` is required by the suggest schema, and the reviewed workflow opts
            # into `enforce_schema=True` — an incomplete selection is a recoverable
            # planning failure there rather than a silently different template choice.
            return {
                "hook": "transformation",
                "structure": "case-loop",
                "visual": "stat-hero",
                "reason": "the idea leads with a number",
            }
        return self.written


@pytest.fixture
def asset(session: Session, tmp_path, monkeypatch) -> Asset:
    """A real 8x8 PNG on disk, in a temporary media directory.

    The `session` fixture rolls its transaction back; nothing rolls back a file, so the media
    directory is redirected the way `test_api_assets.py` does it.
    """
    monkeypatch.setattr(settings, "media_dir", tmp_path)
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), "orange").save(buffer, format="PNG")
    raw = buffer.getvalue()

    digest = sha256_of(raw)
    filename, width, height = store_image(raw, digest, "wordmark.png", "image/png")
    row = Asset(
        filename=filename,
        label="Pixii wordmark",
        kind=AssetKind.LOGO,
        width=width,
        height=height,
        sha256=digest,
    )
    session.add(row)
    session.flush()
    return row


def image_slots(default_asset_id: object = None) -> list[dict]:
    """`stat-hero` v2's slots, optionally with a default on the right-hand image.

    `default_asset_id` is passed through untouched so a test can hand it the JSON number `7`
    or the string `"7"` — both shapes exist and both must resolve to the same asset.
    """
    right: dict = {"name": "right_image_url", "type": "image_url"}
    if default_asset_id is not None:
        right["default_asset_id"] = default_asset_id
    return [
        {"name": "big_number", "type": "text"},
        {"name": "headline", "type": "text"},
        {"name": "left_image_url", "type": "image_url"},
        right,
    ]


def library(session: Session, *, slots: list[dict] | None = None) -> tuple[Template, ...]:
    hook = create_template(
        session,
        kind=TemplateKind.HOOK,
        name="transformation",
        body={"pattern": "{small} turned into {large}"},
        provenance=["win-1"],
    )
    structure = create_template(
        session,
        kind=TemplateKind.STRUCTURE,
        name="case-loop",
        body={"post_type": "case-study", "sections": [{"name": "result", "guidance": "Lead."}]},
    )
    visual = create_template(
        session,
        kind=TemplateKind.VISUAL,
        name="stat-hero",
        body={"renderer": "html", "html": STAT_HERO},
        slots=slots if slots is not None else image_slots(),
    )
    for template in (hook, structure, visual):
        approve(session, template)
    return hook, structure, visual


def generated(
    session: Session,
    captured: dict,
    *,
    asset_values: dict[str, str] | None = None,
    slots: list[dict] | None = None,
) -> Draft:
    hook, structure, visual = library(session, slots=slots)
    return generate_draft(
        session,
        FakeLLM(),
        renderer_capturing(captured, png=b"REALPNG"),
        FakeImageRenderer(),
        idea="a nine figure exit",
        hook_id=hook.id,
        structure_id=structure.id,
        visual_id=visual.id,
        asset_values=asset_values,
    )


# --- the picked asset reaches the picture -------------------------------------------------


def test_a_visual_with_image_slots_completes_for_the_first_time(session, asset):
    """The payoff. Two `image_url` slots, both filled from the library, one real render."""
    captured: dict = {}
    ref = str(asset.id)

    draft = generated(
        session, captured, asset_values={"left_image_url": ref, "right_image_url": ref}
    )

    assert draft.visual_error is None
    assert draft.visual_image == b"REALPNG"
    # Byte-identical, not merely "contains base64": `fill()` escapes slot values, so this is
    # what proves the escaping left the URI alone. A substring check for "base64" would still
    # pass on a mangled one.
    assert data_uri(asset) in captured["html"]
    assert captured["html"].count("data:image/png;base64,") == 2
    assert "{left_image_url}" not in captured["html"]


def test_the_draft_keeps_the_asset_id_and_not_the_data_uri(session, asset):
    """What is stored is the reference. A stored data URI would be a megabyte per draft, and
    a re-render could no longer tell which asset it came from."""
    draft = generated(session, {}, asset_values={"left_image_url": str(asset.id)})

    assert draft.asset_values == {"left_image_url": str(asset.id)}
    assert "left_image_url" not in draft.visual_values


def test_the_words_and_the_assets_stay_in_separate_columns(session, asset):
    draft = generated(session, {}, asset_values={"left_image_url": str(asset.id)})

    assert draft.visual_values == {
        "big_number": "$325M",
        "headline": "One image. Every month.",
    }
    assert draft.asset_values == {"left_image_url": str(asset.id)}


def test_redrawing_embeds_the_asset_again(session, asset):
    """`regenerate_visual` is handed no values — it reads `asset_values` off the draft.

    Twice, deliberately: resolution that wrote its result back onto the draft would pass the
    first call and fail the second with a data URI where an id belongs.
    """
    ref = str(asset.id)
    draft = generated(session, {}, asset_values={"left_image_url": ref, "right_image_url": ref})

    for _ in range(2):
        captured: dict = {}
        regenerate_visual(session, draft, renderer_capturing(captured, png=b"AGAIN"))

        assert draft.visual_error is None
        assert data_uri(asset) in captured["html"]
        assert draft.asset_values["left_image_url"] == str(asset.id)


def test_rewriting_the_words_does_not_discard_the_picked_assets(session, asset):
    draft = generated(session, {}, asset_values={"left_image_url": str(asset.id)})

    regenerate_text(session, FakeLLM(), draft)

    assert draft.asset_values == {"left_image_url": str(asset.id)}


def test_an_unpicked_image_slot_still_fails_by_name(session, asset):
    """Absent stays absent. Half-filling must name the slot nobody chose for."""
    captured: dict = {}

    draft = generated(session, captured, asset_values={"left_image_url": str(asset.id)})

    assert draft.visual_image is None
    assert draft.visual_error is not None
    assert "MissingSlotValue" in draft.visual_error
    assert "right_image_url" in draft.visual_error


def test_a_picked_asset_that_is_not_in_the_library_fails_loudly(session):
    draft = generated(session, {}, asset_values={"left_image_url": "9999"})

    assert draft.visual_image is None
    assert draft.visual_error is not None
    assert "UnresolvableAsset" in draft.visual_error


# --- the template default: completing with nobody picking ---------------------------------


def test_a_template_default_completes_with_no_human_picking(session, asset):
    """`default_asset_id` on every image slot means an unattended run renders a real image."""
    captured: dict = {}
    ref = asset.id

    draft = generated(
        session,
        captured,
        asset_values=None,
        slots=[
            {"name": "big_number", "type": "text"},
            {"name": "headline", "type": "text"},
            {"name": "left_image_url", "type": "image_url", "default_asset_id": ref},
            {"name": "right_image_url", "type": "image_url", "default_asset_id": ref},
        ],
    )

    assert draft.visual_error is None
    assert draft.visual_image == b"REALPNG"
    assert draft.asset_values == {
        "left_image_url": str(ref),
        "right_image_url": str(ref),
    }
    assert captured["html"].count("data:image/png;base64,") == 2


def test_a_default_written_as_a_json_number_and_as_a_string_are_one_reference(session, asset):
    """US-008's delete guard compares `->> 'default_asset_id'` as text, so both shapes must
    normalise to the same stored value or a template default would be invisible to it."""
    as_number = chosen_assets(
        Template(
            family_id="f",
            kind=TemplateKind.VISUAL,
            name="v",
            body={},
            slots=image_slots(default_asset_id=asset.id),
        ),
        {},
    )
    as_string = chosen_assets(
        Template(
            family_id="f",
            kind=TemplateKind.VISUAL,
            name="v",
            body={},
            slots=image_slots(default_asset_id=str(asset.id)),
        ),
        {},
    )

    assert as_number == as_string == {"right_image_url": str(asset.id)}


def test_a_pick_beats_the_template_default(session, asset):
    other = Asset(
        filename=asset.filename,
        label="second",
        kind=AssetKind.PRODUCT,
        width=8,
        height=8,
        sha256="other-digest",
    )
    session.add(other)
    session.flush()

    draft = generated(
        session,
        {},
        asset_values={"right_image_url": str(other.id)},
        slots=image_slots(default_asset_id=asset.id),
    )

    assert draft.asset_values["right_image_url"] == str(other.id)


def test_an_untyped_slot_gets_no_default(session, asset):
    """VISUAL row 556 carries four slots with no `type` key at all. A default on one of them
    is not honoured, for the same reason an untyped slot is not model-writable: absent means
    unknown, and filling an unknown slot with an `<img src>` is the empty-box failure."""
    visual = Template(
        family_id="stat-hero",
        kind=TemplateKind.VISUAL,
        name="stat-hero",
        body={"renderer": "html", "html": STAT_HERO},
        slots=[{"name": "left_image_url", "default_asset_id": asset.id}],
    )

    assert chosen_assets(visual, {}) == {}


# --- the picker is not a second way to write prose ----------------------------------------


def test_the_model_still_may_not_write_an_image_slot():
    assert WRITABLE_SLOT_TYPES == {"text", "number"}


def test_a_picked_value_for_a_text_slot_is_dropped(session, asset):
    """`asset_values` arrives from a request body. Accepting it wholesale would be a second,
    unguarded way to write `big_number` — the hole `WRITABLE_SLOT_TYPES` closes, one column
    over."""
    draft = generated(
        session,
        {},
        asset_values={"left_image_url": str(asset.id), "big_number": "$0", "nonsense": "x"},
    )

    assert draft.asset_values == {"left_image_url": str(asset.id)}
    assert draft.visual_values["big_number"] == "$325M"


def test_an_empty_pick_falls_through_to_the_default(session, asset):
    """A picker that sends `""` for an untouched slot must not shadow the template default."""
    values = chosen_assets(
        Template(
            family_id="f",
            kind=TemplateKind.VISUAL,
            name="v",
            body={},
            slots=image_slots(default_asset_id=asset.id),
        ),
        {"right_image_url": "  "},
    )

    assert values == {"right_image_url": str(asset.id)}


# --- lineage: the asset survives outside our database -------------------------------------


def zernio_capturing(captured: dict) -> ZernioClient:
    """Captures the create call, and answers the two upload calls that now precede it.

    These drafts carry a rendered visual, so a push uploads it before creating the post.
    Only the create body is captured — this file is about lineage metadata, and the upload
    is pinned in test_publishing.py.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "PUT":
            return httpx.Response(200)
        if request.url.path.endswith("/media/presign"):
            return httpx.Response(
                200,
                json={
                    "uploadUrl": "https://store.test/temp/x?sig=1",
                    "publicUrl": "https://media.zernio.test/temp/x.png",
                },
            )
        captured["body"] = json.loads(request.content) if request.content else {}
        return httpx.Response(201, json={"post": {"_id": "zpost-1"}})

    return ZernioClient(
        api_key="k", base_url="https://example.test/api/v1", transport=httpx.MockTransport(handler)
    )


def test_asset_values_reach_the_zernio_metadata_payload(session, asset):
    """Not `lineage_metadata(draft)` alone — the body that actually goes over the wire."""
    captured: dict = {}
    ref = str(asset.id)
    draft = generated(session, {}, asset_values={"left_image_url": ref, "right_image_url": ref})

    push_draft(session, draft, zernio_capturing(captured), account_id="acct-1")

    metadata = captured["body"]["metadata"]
    assert metadata["asset_values"] == {"left_image_url": ref, "right_image_url": ref}
    assert metadata["source"] == "pixii-intelligence"


def test_the_pushed_post_is_still_a_draft(session, asset):
    """Adding asset lineage must not have changed what a push does. Nothing auto-publishes."""
    captured: dict = {}
    draft = generated(session, {}, asset_values={"left_image_url": str(asset.id)})

    push_draft(session, draft, zernio_capturing(captured), account_id="acct-1")

    body = captured["body"]
    assert body["isDraft"] is True
    assert "scheduledFor" not in body
    assert "publishNow" not in body
    assert "queuedFromProfile" not in body


def test_a_visual_with_no_assets_carries_no_empty_object(session):
    """An empty dict in every post's metadata would read as "assets were considered"."""
    draft = generated(session, {}, slots=[{"name": "big_number", "type": "text"}])

    assert "asset_values" not in lineage_metadata(draft)


# --- the DraftOut trap --------------------------------------------------------------------


@pytest.fixture
def client(session: Session) -> Iterator[TestClient]:
    app.dependency_overrides[get_session] = lambda: session
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_a_new_draft_column_reaches_the_api(client, session, asset):
    """`DraftOut` is hand-mapped in two places (`api_drafts.py:33` and `:72`), so a new column
    has a green migration, a green query and a green test while the UI shows nothing.

    Asserted against the serialised HTTP body rather than the model, because the model is
    exactly what would still be right while the response was wrong.
    """
    ref = str(asset.id)
    draft = generated(session, {}, asset_values={"left_image_url": ref})

    body = client.get(f"/drafts/{draft.id}").json()

    assert body["asset_values"] == {"left_image_url": ref}
    assert body["visual_values"]["big_number"] == "$325M"


def test_the_list_endpoint_exposes_it_too(client, session, asset):
    ref = str(asset.id)
    generated(session, {}, asset_values={"left_image_url": ref})

    body = client.get("/drafts").json()

    assert body[0]["asset_values"] == {"left_image_url": ref}


def test_the_api_accepts_a_pick_and_hands_back_what_it_stored(client, session, asset):
    """The request-to-response round trip: `asset_values` on `IdeaIn` survives a real request.

    Against `POST /drafts`, which is deliberately not the browser's path any more — Studio
    generates through `POST /drafts/workflow` and this route is the deprecated unreviewed one.
    It is kept here because `IdeaIn` is the same body on both and this is the shortest request
    that reaches a renderer, so what is asserted is the field surviving the wire rather than
    anything about how a person generates a draft. The draft it produces is `unreviewed` and
    cannot be pushed; `test_review_boundary.py` is where that is asserted.
    """
    captured: dict = {}
    _, _, visual = library(session)
    app.dependency_overrides[get_llm] = lambda: FakeLLM()
    app.dependency_overrides[get_html_renderer] = lambda: renderer_capturing(
        captured, png=b"REALPNG"
    )
    ref = str(asset.id)

    response = client.post(
        "/drafts",
        json={
            "idea": "a nine figure exit",
            "visual_id": visual.id,
            "asset_values": {"left_image_url": ref, "right_image_url": ref},
        },
    )

    assert response.status_code == 201
    assert response.json()["asset_values"] == {"left_image_url": ref, "right_image_url": ref}
    assert response.json()["visual_error"] is None
    assert data_uri(asset) in captured["html"]


# --- unattended: the whole point of a template default ------------------------------------


def test_an_unattended_run_renders_a_real_image_from_a_template_default(session, asset):
    """`run_autonomous` passes no assets at all, so this is the only way a visual carrying
    images can complete without a human. Asserted on the posted markup, not on `created`."""
    captured: dict = {}
    ref = asset.id
    library(
        session,
        slots=[
            {"name": "big_number", "type": "text"},
            {"name": "headline", "type": "text"},
            {"name": "left_image_url", "type": "image_url", "default_asset_id": ref},
            {"name": "right_image_url", "type": "image_url", "default_asset_id": ref},
        ],
    )
    session.add(Post(zernio_id="p1", platform="linkedin", content="a previous post"))
    session.flush()

    result = run_autonomous(
        session,
        FakeLLM({**WRITTEN, "topics": [{"idea": "one"}]}),
        # The idea resolves to `none`, so no provider is reached; a call here is a finding.
        NoSearch(),
        renderer_capturing(captured, png=b"REALPNG"),
        FakeImageRenderer(),
        cap=1,
    )

    assert result.created == 1
    assert result.visuals_failed == 0
    assert captured["html"].count("data:image/png;base64,") == 2


def test_the_autonomous_endpoint_reports_a_failed_visual(client, session, asset):
    """The endpoint's body is the whole of what a caller sees, so the count has to be in it.

    Every image slot carries a default, and that is load-bearing now rather than incidental:
    the run is on the reviewed path, so a slot nothing can fill is a `visual_slot_missing`
    finding and the draft stops at `failed_review` *before* a renderer is asked for anything.
    The failure under test is the renderer's, and it can only happen to a candidate that got
    that far.
    """

    class BrokenRenderer:
        def screenshot(self, html: str, width: int, height: int) -> bytes:
            raise RuntimeError("rendering service unavailable")

    library(
        session,
        slots=[
            {"name": "big_number", "type": "text"},
            {"name": "headline", "type": "text"},
            # Both images, not just the right one: `image_slots` defaults only the right, and
            # a single unfillable slot is enough to stop the candidate at the gate.
            {"name": "left_image_url", "type": "image_url", "default_asset_id": asset.id},
            {"name": "right_image_url", "type": "image_url", "default_asset_id": asset.id},
        ],
    )
    session.add(Post(zernio_id="p1", platform="linkedin", content="a previous post"))
    session.flush()
    app.dependency_overrides[get_llm] = lambda: FakeLLM({**WRITTEN, "topics": [{"idea": "one"}]})
    app.dependency_overrides[get_html_renderer] = BrokenRenderer

    body = client.post("/drafts/autonomous-run?cap=1").json()

    assert body["created"] == 1
    assert body["visuals_failed"] == 1


# --- the delete guard has to follow the writer --------------------------------------------


def test_a_draft_holding_the_asset_in_the_new_column_still_blocks_the_delete(session, asset):
    """US-008's guard read `visual_values`, which is where an id could be at the time. This
    slice moved the writer to `asset_values`; a guard that did not follow would let the only
    copy of the file be deleted out from under a draft that renders from it."""
    generated(session, {}, asset_values={"left_image_url": str(asset.id)})

    holders = holders_of(session, asset.id or 0)

    assert holders and "left_image_url" in holders[0]
    with pytest.raises(AssetInUse):
        delete_asset(session, asset)


def test_a_template_default_blocks_the_delete_too(session, asset):
    library(session, slots=image_slots(default_asset_id=asset.id))

    holders = holders_of(session, asset.id or 0)

    assert any("stat-hero" in h and "right_image_url" in h for h in holders)
