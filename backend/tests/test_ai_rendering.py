import base64
import io
import json

import httpx
import pytest
from PIL import Image

from app.models.template import Template, TemplateKind
from app.rendering import (
    AzureImageRenderer,
    ImageGenerationError,
    MissingSlotValue,
    UnsupportedRenderer,
    render_visual,
    snap_to_16,
)


def png_bytes(width: int, height: int) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (245, 240, 232)).save(buffer, format="PNG")
    return buffer.getvalue()


def renderer_capturing(captured: dict, *, generated=(1088, 1360)) -> AzureImageRenderer:
    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        captured["path"] = request.url.path
        captured["key"] = request.headers.get("api-key")
        encoded = base64.b64encode(png_bytes(*generated)).decode()
        return httpx.Response(200, json={"data": [{"b64_json": encoded}]})

    return AzureImageRenderer(
        endpoint="https://example.test",
        api_key="k",
        deployment="gpt-image-2",
        api_version="2025-04-01-preview",
        transport=httpx.MockTransport(handler),
    )


def ai_visual(**body) -> Template:
    payload = {
        "renderer": "ai",
        "prompt": "a {subject} on a plain cream background",
        "style_reference": "flat product photography, soft shadow, #F5F0E8 field",
    }
    payload.update(body)
    return Template(
        family_id="fam",
        kind=TemplateKind.VISUAL,
        name="lifestyle-scene",
        body=payload,
        slots=[{"name": "subject", "example": "ceramic jar"}],
    )


def test_an_ai_template_persists_its_prompt_skeleton_and_style_reference():
    template = ai_visual()

    assert "{subject}" in template.body["prompt"]
    assert template.body["style_reference"].startswith("flat product photography")


def test_the_prompt_skeleton_is_filled_from_slot_values():
    captured: dict = {}

    render_visual(ai_visual(), {"subject": "ceramic jar"}, renderer_capturing(captured))

    assert "ceramic jar" in captured["prompt"]
    assert "{subject}" not in captured["prompt"]


def test_the_style_reference_is_carried_into_every_prompt():
    """It is what keeps generated imagery on brand between runs."""
    captured: dict = {}

    render_visual(ai_visual(), {"subject": "jar"}, renderer_capturing(captured))

    assert "flat product photography" in captured["prompt"]


def test_a_missing_slot_value_is_an_error_not_a_literal_placeholder():
    with pytest.raises(MissingSlotValue):
        render_visual(ai_visual(), {}, renderer_capturing({}))


def test_the_generated_image_matches_the_templates_declared_dimensions():
    image = render_visual(ai_visual(), {"subject": "jar"}, renderer_capturing({}))

    assert Image.open(io.BytesIO(image)).size == (1080, 1350)


def test_generation_requests_a_size_the_service_actually_accepts():
    """Azure rejects any dimension not divisible by 16, so 1080x1350 cannot be asked for."""
    captured: dict = {}

    render_visual(ai_visual(), {"subject": "jar"}, renderer_capturing(captured))

    width, height = (int(n) for n in captured["size"].split("x"))
    assert width % 16 == 0 and height % 16 == 0
    assert captured["size"] == "1088x1360"


def test_snap_to_16_rounds_up_and_leaves_valid_sizes_alone():
    assert snap_to_16(1080) == 1088
    assert snap_to_16(1350) == 1360
    assert snap_to_16(1024) == 1024


def test_a_template_may_declare_its_own_dimensions():
    image = render_visual(
        ai_visual(width=1024, height=1024),
        {"subject": "jar"},
        renderer_capturing({}, generated=(1024, 1024)),
    )

    assert Image.open(io.BytesIO(image)).size == (1024, 1024)


def test_targets_the_azure_image_deployment():
    captured: dict = {}

    render_visual(ai_visual(), {"subject": "jar"}, renderer_capturing(captured))

    assert captured["path"] == "/openai/deployments/gpt-image-2/images/generations"
    assert captured["key"] == "k"


def test_a_refusal_is_surfaced_with_its_reason_not_as_a_blank_image():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400, json={"error": {"message": "content policy violation", "code": "moderation"}}
        )

    renderer = AzureImageRenderer(
        endpoint="https://e.test",
        api_key="k",
        deployment="d",
        api_version="v",
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(ImageGenerationError) as raised:
        render_visual(ai_visual(), {"subject": "jar"}, renderer)

    assert "content policy" in str(raised.value)


def test_a_response_carrying_no_image_is_an_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": []})

    renderer = AzureImageRenderer(
        endpoint="https://e.test",
        api_key="k",
        deployment="d",
        api_version="v",
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(ImageGenerationError):
        render_visual(ai_visual(), {"subject": "jar"}, renderer)


def test_an_html_template_is_not_sent_to_the_image_service():
    html_template = Template(
        family_id="f",
        kind=TemplateKind.VISUAL,
        name="stat-hero",
        body={"renderer": "html", "html": "<b>{headline}</b>"},
    )

    with pytest.raises(UnsupportedRenderer):
        render_visual(html_template, {"headline": "hi"}, renderer_capturing({}))
