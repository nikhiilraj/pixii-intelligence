import httpx
import pytest

from app.llm import AzureChat, LLMResponseError


def chat_returning(handler) -> AzureChat:
    return AzureChat(
        endpoint="https://example.test",
        api_key="k",
        deployment="gpt-5-5",
        api_version="2024-12-01-preview",
        transport=httpx.MockTransport(handler),
    )


def completion(content: str) -> dict:
    return {"choices": [{"message": {"content": content}}]}


def test_returns_the_parsed_json_the_model_produced():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=completion('{"hooks": [{"name": "transformation"}]}'))

    result = chat_returning(handler).complete_json("system", "user")

    assert result == {"hooks": [{"name": "transformation"}]}


def test_tolerates_a_model_that_wraps_json_in_a_code_fence():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=completion('```json\n{"hooks": []}\n```'))

    assert chat_returning(handler).complete_json("s", "u") == {"hooks": []}


def test_unparseable_output_is_an_error_not_an_empty_result():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=completion("I'm afraid I can't do that."))

    with pytest.raises(LLMResponseError):
        chat_returning(handler).complete_json("s", "u")


def test_sends_the_api_key_header_and_targets_the_deployment():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["key"] = request.headers.get("api-key")
        seen["path"] = request.url.path
        seen["version"] = request.url.params.get("api-version")
        return httpx.Response(200, json=completion("{}"))

    chat_returning(handler).complete_json("s", "u")

    assert seen["key"] == "k"
    assert seen["path"] == "/openai/deployments/gpt-5-5/chat/completions"
    assert seen["version"] == "2024-12-01-preview"


def test_uses_max_completion_tokens_not_max_tokens():
    """This deployment rejects max_tokens — the seam notes record it."""
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        seen.update(json.loads(request.content))
        return httpx.Response(200, json=completion("{}"))

    chat_returning(handler).complete_json("s", "u")

    assert "max_completion_tokens" in seen
    assert "max_tokens" not in seen


def test_an_http_error_is_raised_rather_than_swallowed():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": "rate limited"})

    with pytest.raises(httpx.HTTPStatusError):
        chat_returning(handler).complete_json("s", "u")
