import json

import httpx
import pytest

from app.models.template import Template, TemplateKind
from app.rendering import (
    CloudflareRenderer,
    MissingSlotValue,
    UnsupportedRenderer,
    fill,
    render_visual,
)

STAT_HERO = (
    '<div class="card"><span class="ghost">{big_number}</span>'
    "<h1>{headline}</h1><img src=\"{logo_url}\"></div>"
)


def visual(**body) -> Template:
    payload = {"renderer": "html", "html": STAT_HERO}
    payload.update(body)
    return Template(
        family_id="fam",
        kind=TemplateKind.VISUAL,
        name="stat-hero",
        body=payload,
        slots=[{"name": "big_number"}, {"name": "headline"}, {"name": "logo_url"}],
    )


def renderer_capturing(captured: dict, png: bytes = b"\x89PNG") -> CloudflareRenderer:
    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        captured["path"] = request.url.path
        captured["auth"] = request.headers.get("authorization")
        return httpx.Response(200, content=png)

    return CloudflareRenderer(
        account_id="acct", token="tok", transport=httpx.MockTransport(handler)
    )


VALUES = {
    "big_number": "$325M",
    "headline": "A 9-figure exit",
    "logo_url": "https://example.test/logo.png",
}


def test_renders_at_the_linkedin_portrait_size_by_default():
    captured: dict = {}

    render_visual(visual(), VALUES, renderer_capturing(captured))

    assert captured["viewport"] == {"width": 1080, "height": 1350}


def test_a_template_may_declare_its_own_dimensions():
    captured: dict = {}

    render_visual(visual(width=1200, height=1200), VALUES, renderer_capturing(captured))

    assert captured["viewport"] == {"width": 1200, "height": 1200}


def test_returns_the_rendered_image_bytes():
    assert render_visual(visual(), VALUES, renderer_capturing({}, png=b"IMAGE")) == b"IMAGE"


def test_every_slot_value_reaches_the_rendered_html():
    captured: dict = {}

    render_visual(visual(), VALUES, renderer_capturing(captured))

    assert "$325M" in captured["html"]
    assert "A 9-figure exit" in captured["html"]
    assert "{big_number}" not in captured["html"]


def test_a_missing_slot_value_is_an_error_not_a_literal_placeholder():
    """Rendering '{headline}' as visible text would ship a broken image."""
    with pytest.raises(MissingSlotValue):
        render_visual(visual(), {"big_number": "$1", "logo_url": "x"}, renderer_capturing({}))


def test_slot_values_cannot_break_out_of_the_layout():
    """Values will come from a language model, so they are escaped, not trusted."""
    captured: dict = {}
    values = {**VALUES, "headline": '</div><script>alert(1)</script>'}

    render_visual(visual(), values, renderer_capturing(captured))

    assert "<script>" not in captured["html"]
    assert "&lt;script&gt;" in captured["html"]


def test_an_ai_template_is_not_rendered_by_the_html_path():
    ai_template = visual(renderer="ai", prompt="a cream studio scene")

    with pytest.raises(UnsupportedRenderer):
        render_visual(ai_template, VALUES, renderer_capturing({}))


def test_a_template_with_no_renderer_declared_is_rejected():
    template = Template(family_id="f", kind=TemplateKind.VISUAL, name="bare", body={})

    with pytest.raises(UnsupportedRenderer):
        render_visual(template, {}, renderer_capturing({}))


def test_targets_the_cloudflare_browser_rendering_endpoint():
    captured: dict = {}

    render_visual(visual(), VALUES, renderer_capturing(captured))

    assert captured["path"] == "/client/v4/accounts/acct/browser-rendering/screenshot"
    assert captured["auth"] == "Bearer tok"


def test_an_http_failure_is_raised_rather_than_returning_an_empty_image():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"errors": ["nope"]})

    renderer = CloudflareRenderer(
        account_id="a", token="t", transport=httpx.MockTransport(handler)
    )

    with pytest.raises(httpx.HTTPStatusError):
        render_visual(visual(), VALUES, renderer)


RATE_LIMITED = {"success": False, "errors": [{"code": 2001, "message": "Rate limit exceeded"}]}


def renderer_over(statuses: list[int], sleeps: list[float]) -> tuple[CloudflareRenderer, list]:
    """A renderer that answers `statuses` in order, recording every sleep instead of taking it.

    Asserting the recorded delays rather than elapsed time is what keeps this suite at ~3
    seconds: no test here ever really waits, and the retry policy is pinned as data.
    """
    attempts: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(request)
        status = statuses[min(len(attempts) - 1, len(statuses) - 1)]
        if status == 429:
            return httpx.Response(429, json=RATE_LIMITED)
        return httpx.Response(status, content=b"PNG" if status < 400 else b"nope")

    renderer = CloudflareRenderer(
        account_id="a",
        token="t",
        transport=httpx.MockTransport(handler),
        sleep=sleeps.append,
    )
    return renderer, attempts


def test_a_rate_limited_render_backs_off_and_then_succeeds():
    """429 code 2001 is a cadence problem, not a payload problem — waiting fixes it."""
    sleeps: list[float] = []
    renderer, attempts = renderer_over([429, 429, 200], sleeps)

    assert render_visual(visual(), VALUES, renderer) == b"PNG"
    assert len(attempts) == 3
    assert sleeps == [5.0, 10.0]


def test_a_persistent_rate_limit_raises_rather_than_retrying_forever():
    """Bounded attempts and bounded total wait: 4 tries, 35 seconds, then the error surfaces."""
    sleeps: list[float] = []
    renderer, attempts = renderer_over([429], sleeps)

    with pytest.raises(httpx.HTTPStatusError):
        render_visual(visual(), VALUES, renderer)

    assert len(attempts) == 4
    assert sleeps == [5.0, 10.0, 20.0]
    assert sum(sleeps) == 35.0


def test_a_server_error_is_not_retried():
    """Only 429 means 'come back later'. A 500 is real and must not be waited out."""
    sleeps: list[float] = []
    renderer, attempts = renderer_over([500], sleeps)

    with pytest.raises(httpx.HTTPStatusError):
        render_visual(visual(), VALUES, renderer)

    assert len(attempts) == 1
    assert sleeps == []


def test_a_client_error_is_not_retried():
    sleeps: list[float] = []
    renderer, attempts = renderer_over([403], sleeps)

    with pytest.raises(httpx.HTTPStatusError):
        render_visual(visual(), VALUES, renderer)

    assert len(attempts) == 1
    assert sleeps == []


def test_fill_substitutes_only_named_slots_and_leaves_css_braces_alone():
    """CSS in a template is full of braces — they must survive substitution."""
    template = "<style>.card { color: red; }</style><b>{headline}</b>"

    filled = fill(template, {"headline": "hi"})

    assert ".card { color: red; }" in filled
    assert "<b>hi</b>" in filled
