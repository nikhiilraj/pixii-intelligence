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
