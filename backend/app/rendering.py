import html as html_escape
import re
from typing import Protocol

import httpx

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
    """The template declares a renderer this path cannot produce."""


class HtmlRenderer(Protocol):
    """Turns HTML into image bytes at a given size."""

    def screenshot(self, html: str, width: int, height: int) -> bytes: ...


def fill(template_html: str, values: dict[str, str]) -> str:
    """Substitute {slot} placeholders, escaping every value.

    Template chrome is authored by the team and trusted. Slot values are not — they will
    come from a language model — so they are escaped rather than injected raw.
    """
    filled = template_html
    for name, value in values.items():
        filled = filled.replace(f"{{{name}}}", html_escape.escape(str(value)))

    unresolved = sorted(set(_SLOT.findall(filled)))
    if unresolved:
        raise MissingSlotValue(f"no value supplied for: {', '.join(unresolved)}")
    return filled


class CloudflareRenderer:
    """HTML to PNG via Cloudflare Browser Rendering. Verified live 2026-07-29."""

    def __init__(
        self,
        account_id: str | None = None,
        token: str | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._account_id = account_id or settings.cloudflare_account_id
        bearer = token or settings.cloudflare_browser_rendering_token
        self._client = httpx.Client(
            base_url="https://api.cloudflare.com",
            headers={"Authorization": f"Bearer {bearer}"},
            timeout=120.0,
            transport=transport,
        )

    def screenshot(self, html: str, width: int, height: int) -> bytes:
        response = self._client.post(
            f"/client/v4/accounts/{self._account_id}/browser-rendering/screenshot",
            json={
                "html": html,
                "viewport": {"width": width, "height": height},
                "screenshotOptions": {"type": "png"},
            },
        )
        response.raise_for_status()
        return response.content

    def close(self) -> None:
        self._client.close()


def render_visual(
    template: Template, values: dict[str, str], renderer: HtmlRenderer
) -> bytes:
    """Render a visual template to image bytes, by the renderer it declares.

    ponytail: the html path is the only one implemented. The ai path arrives in US-008 and
    dispatches from the same declaration.
    """
    declared = template.body.get("renderer")
    if declared != "html":
        raise UnsupportedRenderer(
            f"template {template.name!r} declares renderer {declared!r}; "
            "this path renders 'html' templates only"
        )

    markup = template.body.get("html") or ""
    if not markup:
        raise UnsupportedRenderer(f"html template {template.name!r} carries no markup")

    return renderer.screenshot(
        fill(markup, values),
        int(template.body.get("width") or DEFAULT_WIDTH),
        int(template.body.get("height") or DEFAULT_HEIGHT),
    )
