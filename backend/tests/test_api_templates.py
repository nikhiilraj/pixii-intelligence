from fastapi.testclient import TestClient
from sqlmodel import Session

from app.db import get_session
from app.main import app


def client_with(session: Session) -> TestClient:
    app.dependency_overrides[get_session] = lambda: session
    return TestClient(app)


HOOK = {
    "kind": "hook",
    "name": "transformation",
    "body": {"pattern": "{small_value} turned into {large_value} {unit}"},
    "slots": [{"name": "small_value", "example": "$450"}],
    "provenance": ["6a26f1402b2567671a2daa62"],
}


def test_authoring_a_template_returns_it_awaiting_approval(session):
    body = client_with(session).post("/templates", json=HOOK).json()

    assert body["version"] == 1
    assert body["status"] == "proposed"
    assert body["kind"] == "hook"
    app.dependency_overrides.clear()


def test_revising_returns_a_new_version_and_keeps_the_old_one(session):
    client = client_with(session)
    original = client.post("/templates", json=HOOK).json()

    revised = client.put(f"/templates/{original['id']}", json={"name": "renamed"}).json()

    assert revised["version"] == 2
    assert revised["name"] == "renamed"
    versions = client.get(f"/templates/{revised['id']}/versions").json()
    assert [v["version"] for v in versions] == [1, 2]
    assert versions[0]["name"] == "transformation"
    app.dependency_overrides.clear()


def test_approving_makes_a_template_usable_for_generation(session):
    client = client_with(session)
    created = client.post("/templates", json=HOOK).json()

    assert client.get("/templates?kind=hook&usable_only=true").json() == []

    client.post(f"/templates/{created['id']}/approve")

    usable = client.get("/templates?kind=hook&usable_only=true").json()
    assert [t["name"] for t in usable] == ["transformation"]
    app.dependency_overrides.clear()


def test_retiring_removes_it_from_use_but_not_from_the_record(session):
    client = client_with(session)
    created = client.post("/templates", json=HOOK).json()
    client.post(f"/templates/{created['id']}/approve")

    client.post(f"/templates/{created['id']}/retire")

    assert client.get("/templates?kind=hook&usable_only=true").json() == []
    assert client.get(f"/templates/{created['id']}/versions").json()[0]["status"] == "retired"
    app.dependency_overrides.clear()


def test_revising_a_retired_template_is_rejected(session):
    client = client_with(session)
    created = client.post("/templates", json=HOOK).json()
    client.post(f"/templates/{created['id']}/retire")

    response = client.put(f"/templates/{created['id']}", json={"name": "resurrect"})

    assert response.status_code == 409
    app.dependency_overrides.clear()


def test_an_unknown_template_is_not_found(session):
    assert client_with(session).get("/templates/999999/versions").status_code == 404
    app.dependency_overrides.clear()


def test_listing_shows_only_the_current_version_of_each_family(session):
    client = client_with(session)
    created = client.post("/templates", json=HOOK).json()
    client.put(f"/templates/{created['id']}", json={"name": "v2"})

    listed = client.get("/templates?kind=hook").json()

    assert [t["name"] for t in listed] == ["v2"]
    app.dependency_overrides.clear()


def test_extraction_endpoint_creates_proposals_awaiting_approval(session):
    from app.deps import get_llm
    from app.models.post import Post

    session.add(
        Post(zernio_id="win-1", platform="linkedin", content="A hook.", engaged_actions=185)
    )
    session.flush()

    app.dependency_overrides[get_llm] = lambda: _FakeLLM(
        {
            "hooks": [
                {
                    "name": "transformation",
                    "pattern": "{a} turned into {b}",
                    "tone": "plain",
                    "source_post_ids": ["win-1"],
                }
            ]
        }
    )
    client = client_with(session)

    body = client.post("/templates/extract/hooks").json()

    assert [t["status"] for t in body] == ["proposed"]
    assert body[0]["provenance"] == ["win-1"]
    assert client.get("/templates?kind=hook&usable_only=true").json() == []
    app.dependency_overrides.clear()


def test_a_model_that_returns_the_wrong_shape_is_reported_not_silently_ignored(session):
    from app.deps import get_llm
    from app.models.post import Post

    session.add(Post(zernio_id="win-1", platform="linkedin", content="A hook.", engaged_actions=1))
    session.flush()
    app.dependency_overrides[get_llm] = lambda: _FakeLLM({"nope": []})

    response = client_with(session).post("/templates/extract/hooks")

    assert response.status_code == 502
    app.dependency_overrides.clear()


class _FakeLLM:
    def __init__(self, response: dict):
        self.response = response

    def complete_json(self, system: str, user: str) -> dict:
        return self.response


def test_a_failing_render_service_is_reported_as_upstream_not_as_our_crash(session):
    """A 429 from the renderer must not surface as an opaque 500."""
    import httpx

    from app.deps import get_html_renderer
    from app.models.template import Template, TemplateKind

    template = Template(
        family_id="f",
        kind=TemplateKind.VISUAL,
        name="stat-hero",
        body={"renderer": "html", "html": "<b>{headline}</b>"},
    )
    session.add(template)
    session.flush()

    class RateLimited:
        def screenshot(self, html: str, width: int, height: int) -> bytes:
            raise httpx.HTTPStatusError(
                "429", request=httpx.Request("POST", "https://x.test"),
                response=httpx.Response(429),
            )

    app.dependency_overrides[get_html_renderer] = lambda: RateLimited()

    response = client_with(session).post(
        f"/templates/{template.id}/preview", json={"headline": "hi"}
    )

    assert response.status_code == 502
    assert "429" in response.json()["detail"]
    app.dependency_overrides.clear()
