from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_reports_status_database_and_credentials():
    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert isinstance(body["database"], bool)
    assert set(body["credentials"]) == {
        "zernio",
        "azure_chat",
        "azure_image",
        "cloudflare_rendering",
    }


def test_health_reports_degraded_when_the_database_is_unreachable(monkeypatch):
    """`status` was the literal `"ok"`, so the frontend's status row was structurally capable
    of red (US-007) and could never actually go red. A readout that can only say "fine" is
    worse than none — the one state it cannot report is the only one anyone needs it for."""
    monkeypatch.setattr("app.main.database_reachable", lambda: False)

    body = client.get("/health").json()

    assert body["status"] == "degraded"
    assert body["database"] is False


def test_health_never_leaks_credential_values():
    """A status endpoint that reports configuration must report presence, not values."""
    body = client.get("/health").json()

    for present in body["credentials"].values():
        assert isinstance(present, bool)
