import json

import httpx
import pytest

from app.config import settings
from app.deps import get_search
from app.research import (
    BraveSearchProvider,
    FirecrawlSearchProvider,
    SearchResult,
    SearchUnavailable,
)


def test_firecrawl_search_uses_v2_contract_and_returns_discovery_metadata():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.headers["Authorization"] == "Bearer canary-not-a-real-key"
        assert json.loads(request.content) == {
            "query": "pixii product photography",
            "limit": 5,
            "sources": ["web"],
        }
        return httpx.Response(
            200,
            json={
                "success": True,
                "data": {
                    "web": [
                        {
                            "url": "https://example.com/article",
                            "title": "Example",
                            "description": "A useful result",
                        },
                        {"url": ""},
                    ]
                },
            },
        )

    provider = FirecrawlSearchProvider(
        "canary-not-a-real-key",
        "https://api.firecrawl.dev/v2/search",
        transport=httpx.MockTransport(handler),
    )
    try:
        assert provider.search("pixii product photography", limit=99) == [
            SearchResult(
                url="https://example.com/article",
                title="Example",
                snippet="A useful result",
            )
        ]
    finally:
        provider.close()


def test_firecrawl_search_refuses_an_unsuccessful_envelope():
    provider = FirecrawlSearchProvider(
        "canary-not-a-real-key",
        "https://api.firecrawl.dev/v2/search",
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(200, json={"success": False, "data": {}})
        ),
    )
    try:
        with pytest.raises(SearchUnavailable, match="unsuccessful"):
            provider.search("anything", limit=1)
    finally:
        provider.close()


def test_firecrawl_is_preferred_and_brave_remains_the_fallback(monkeypatch):
    monkeypatch.setattr(settings, "firecrawl_api_key", "canary-firecrawl")
    monkeypatch.setattr(settings, "brave_search_api_key", "canary-brave")
    dependency = get_search()
    try:
        assert isinstance(next(dependency), FirecrawlSearchProvider)
    finally:
        dependency.close()

    monkeypatch.setattr(settings, "firecrawl_api_key", "")
    dependency = get_search()
    try:
        assert isinstance(next(dependency), BraveSearchProvider)
    finally:
        dependency.close()
