import base64
import html as html_escape
import io
import re
import time
from collections.abc import Callable
from typing import Protocol

import httpx
from PIL import Image

from app.config import settings
from app.models.template import Template

# LinkedIn portrait. Every post visual in the corpus is this size.
DEFAULT_WIDTH = 1080
DEFAULT_HEIGHT = 1350

# A slot looks like {snake_name}. Deliberately narrow so CSS rules — `.card { color: red }`
# — are never mistaken for placeholders. str.format() cannot be used here for that reason.
_SLOT = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


class MissingSlotValue(RuntimeError):
    """A placeholder had no value. Rendering it literally would ship a broken image."""


class UnsupportedRenderer(RuntimeError):
    """The template declares a renderer the supplied renderer cannot produce."""


class ImageGenerationError(RuntimeError):
    """The image service declined or returned nothing usable."""


def snap_to_16(value: int) -> int:
    """Round up to a multiple of 16.

    Azure rejects any dimension not divisible by 16, so the brand's 1080x1350 cannot be
    requested directly. Asking for 1088x1360 keeps the exact 4:5 ratio, and the result is
    scaled down to the declared size — no crop, no distortion.
    """
    return -(-value // 16) * 16


class HtmlRenderer(Protocol):
    """Turns HTML into image bytes at a given size."""

    def screenshot(self, html: str, width: int, height: int) -> bytes: ...


class ImageRenderer(Protocol):
    """Generates an image from a prompt at a given size."""

    def generate(self, prompt: str, width: int, height: int) -> bytes: ...


def fill(template_html: str, values: dict[str, str], *, escape: bool = True) -> str:
    """Substitute {slot} placeholders.

    Template chrome is authored by the team and trusted. Slot values are not — they will
    come from a language model — so for markup they are escaped rather than injected raw.
    Prompts are not markup: escaping there would put &amp; into the text sent to the image
    model, so callers building a prompt pass escape=False.
    """
    filled = template_html
    for name, value in values.items():
        replacement = html_escape.escape(str(value)) if escape else str(value)
        filled = filled.replace(f"{{{name}}}", replacement)

    unresolved = sorted(set(_SLOT.findall(filled)))
    if unresolved:
        raise MissingSlotValue(f"no value supplied for: {', '.join(unresolved)}")
    return filled


# Free-tier Browser Rendering limits by requests-per-minute, not bytes, and answers
# 429 {"errors":[{"code":2001,"message":"Rate limit exceeded"}]} — which reads exactly like a
# payload rejection and already caused one phantom size-ceiling diagnosis. A per-minute window
# cannot be cleared by sub-second retries, so the delays are tens of seconds: 5 + 10 + 20 = 35s
# of waiting across 4 attempts. That is long enough to outlast the window and short enough that
# a render never sits behind the retry loop longer than the 120s request timeout it already has.
_RATE_LIMIT_DELAYS = (5.0, 10.0, 20.0)


class CloudflareRenderer:
    """HTML to PNG via Cloudflare Browser Rendering. Verified live 2026-07-29."""

    def __init__(
        self,
        account_id: str | None = None,
        token: str | None = None,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._account_id = account_id or settings.cloudflare_account_id
        bearer = token or settings.cloudflare_browser_rendering_token
        self._sleep = sleep
        self._client = httpx.Client(
            base_url="https://api.cloudflare.com",
            headers={"Authorization": f"Bearer {bearer}"},
            timeout=120.0,
            transport=transport,
        )

    def screenshot(self, html: str, width: int, height: int) -> bytes:
        """Render HTML to PNG bytes, backing off if the account is rate-limited.

        The 429 is absorbed here so no caller ever sees it — a loop over templates would
        otherwise misreport a cadence problem as a size or format bug. Every other status is
        the API telling us something real, so it is raised on the first attempt.
        """
        for delay in (*_RATE_LIMIT_DELAYS, None):
            response = self._client.post(
                f"/client/v4/accounts/{self._account_id}/browser-rendering/screenshot",
                json={
                    "html": html,
                    "viewport": {"width": width, "height": height},
                    "screenshotOptions": {"type": "png"},
                },
            )
            if response.status_code != 429 or delay is None:
                break
            # ponytail: fixed delays, no jitter and no Retry-After. One process renders one
            # visual at a time, so there is no thundering herd to spread out, and the live
            # probe never saw a Retry-After header to honour. Read it here if one shows up.
            self._sleep(delay)

        # Exhausted retries fall through to the same error every other status takes, so the
        # exception type above screenshot() is unchanged.
        response.raise_for_status()
        return response.content

    def close(self) -> None:
        self._client.close()


def render_visual(
    template: Template, values: dict[str, str], renderer: HtmlRenderer | ImageRenderer
) -> bytes:
    """Render a visual template to image bytes, by the renderer it declares.

    Both renderers are reached through this one call. The template's declaration selects
    the path; supplying a renderer that cannot serve that declaration is an error rather
    than a silent fallback.
    """
    declared = template.body.get("renderer")
    width = int(template.body.get("width") or DEFAULT_WIDTH)
    height = int(template.body.get("height") or DEFAULT_HEIGHT)

    if declared == "html":
        if not hasattr(renderer, "screenshot"):
            raise UnsupportedRenderer(f"template {template.name!r} needs an html renderer")
        markup = template.body.get("html") or ""
        if not markup:
            raise UnsupportedRenderer(f"html template {template.name!r} carries no markup")
        return renderer.screenshot(fill(markup, values), width, height)

    if declared == "ai":
        if not hasattr(renderer, "generate"):
            raise UnsupportedRenderer(f"template {template.name!r} needs an image renderer")
        skeleton = template.body.get("prompt") or ""
        if not skeleton:
            raise UnsupportedRenderer(f"ai template {template.name!r} carries no prompt")
        # The style reference travels with every prompt — it is what holds generated
        # imagery to the brand between runs.
        style = (template.body.get("style_reference") or "").strip()
        prompt = fill(skeleton, values, escape=False)
        described = f"{prompt}. {style}" if style else prompt
        return renderer.generate(described, width, height)

    raise UnsupportedRenderer(
        f"template {template.name!r} declares renderer {declared!r}, which is not supported"
    )


class AzureImageRenderer:
    """Azure OpenAI image generation. Contract verified live 2026-07-29.

    Returns base64 PNG under ``data[0].b64_json`` — this deployment never returns a URL.
    """

    def __init__(
        self,
        endpoint: str | None = None,
        api_key: str | None = None,
        deployment: str | None = None,
        api_version: str | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._deployment = deployment or settings.azure_openai_image_deployment
        self._api_version = api_version or settings.azure_openai_image_api_version
        self._client = httpx.Client(
            base_url=(endpoint or settings.azure_openai_image_endpoint).rstrip("/"),
            headers={"api-key": api_key or settings.azure_openai_image_api_key},
            timeout=300.0,
            transport=transport,
        )

    def generate(self, prompt: str, width: int, height: int) -> bytes:
        response = self._client.post(
            f"/openai/deployments/{self._deployment}/images/generations",
            params={"api-version": self._api_version},
            json={
                "prompt": prompt,
                "n": 1,
                "size": f"{snap_to_16(width)}x{snap_to_16(height)}",
                "quality": "medium",
            },
        )
        if response.status_code >= 400:
            # A refusal is information, not a failure to hide. Surface what it said.
            reason = _error_message(response)
            raise ImageGenerationError(f"image service refused ({response.status_code}): {reason}")

        entries = response.json().get("data") or []
        if not entries or not entries[0].get("b64_json"):
            raise ImageGenerationError("image service returned no image")

        raw = base64.b64decode(entries[0]["b64_json"])
        return _scale_to(raw, width, height)

    def close(self) -> None:
        self._client.close()


def _error_message(response: httpx.Response) -> str:
    try:
        return str(response.json().get("error", {}).get("message", response.text))[:300]
    except Exception:
        return response.text[:300]


def _scale_to(raw: bytes, width: int, height: int) -> bytes:
    """Scale a generated image to the exact declared size."""
    opened = Image.open(io.BytesIO(raw))
    scaled = (
        opened
        if opened.size == (width, height)
        else opened.resize((width, height), Image.Resampling.LANCZOS)
    )
    buffer = io.BytesIO()
    scaled.save(buffer, format="PNG")
    return buffer.getvalue()
