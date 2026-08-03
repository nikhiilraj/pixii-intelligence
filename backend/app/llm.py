import base64
import io
import json
import re
from collections.abc import Sequence
from typing import Protocol

import httpx
from PIL import Image

from app.config import settings

# Models sometimes wrap JSON in a markdown fence despite being told not to.
_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)


class LLMResponseError(RuntimeError):
    """The model answered, but not with the JSON it was asked for."""


class LLM(Protocol):
    """What the rest of the app needs from a language model. One method, one shape."""

    def complete_json(self, system: str, user: str, images: Sequence[bytes] = ()) -> dict: ...


class AzureChat:
    """Azure OpenAI chat completions.

    Note `max_completion_tokens` rather than `max_tokens` — this deployment rejects the
    older parameter. Verified live 2026-07-29.
    """

    def __init__(
        self,
        endpoint: str | None = None,
        api_key: str | None = None,
        deployment: str | None = None,
        api_version: str | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._deployment = deployment or settings.azure_openai_chat_deployment
        self._api_version = api_version or settings.azure_openai_chat_api_version
        self._client = httpx.Client(
            base_url=(endpoint or settings.azure_openai_chat_endpoint).rstrip("/"),
            headers={"api-key": api_key or settings.azure_openai_chat_api_key},
            timeout=180.0,
            transport=transport,
        )

    def complete_json(self, system: str, user: str, images: Sequence[bytes] = ()) -> dict:
        response = self._client.post(
            f"/openai/deployments/{self._deployment}/chat/completions",
            params={"api-version": self._api_version},
            json={
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": _user_content(user, images)},
                ],
                "max_completion_tokens": 16000,
            },
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"] or ""
        return _parse_json(content)

    def close(self) -> None:
        self._client.close()


def _parse_json(content: str) -> dict:
    fenced = _FENCE.match(content)
    if fenced:
        content = fenced.group(1)
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        raise LLMResponseError(f"model did not return JSON: {content[:200]!r}") from exc
    if not isinstance(parsed, dict):
        raise LLMResponseError(f"expected a JSON object, got {type(parsed).__name__}")
    return parsed


def _user_content(user: str, images: Sequence[bytes]) -> str | list[dict]:
    """The user message, as a plain string when there are no images.

    Kept as a string in the no-image case rather than a one-element parts list: every
    existing caller passes no images, and a payload that changed shape for all of them
    would put the whole app behind one untested serialisation difference.
    """
    if not images:
        return user
    return [{"type": "text", "text": user}, *(_image_part(raw) for raw in images)]


def _image_part(raw: bytes) -> dict:
    """One image as a data URI part, typed by what the bytes actually are.

    The mime is sniffed rather than assumed: `media/` holds .png, .jpg, .jpeg and .gif
    side by side, and declaring the wrong one is a 400 from the deployment that reads
    like a prompt problem.
    """
    mime = Image.MIME.get(Image.open(io.BytesIO(raw)).format or "") or "image/png"
    encoded = base64.b64encode(raw).decode("ascii")
    return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}}
