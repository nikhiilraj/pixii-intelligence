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


def test_health_never_leaks_credential_values():
    """A status endpoint that reports configuration must report presence, not values."""
    body = client.get("/health").json()

    for present in body["credentials"].values():
        assert isinstance(present, bool)
