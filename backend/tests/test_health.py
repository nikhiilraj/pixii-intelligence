from fastapi.testclient import TestClient

from app.config import settings
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
        "firecrawl_search",
        "brave_search",
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


def test_health_reports_the_variants_ceiling_as_a_sibling_of_the_credential_flags():
    """Studio's variants control has to be able to say what it will spend.

    Compared against `settings.variants_max`, never the literal 3 — a test hardcoding the
    number would reproduce inside the suite the exact drift this endpoint exists to remove.
    A sibling, not a credential: `credentials` is rendered row-per-key by the Inbox footer,
    so a scalar in there would render as a junk boolean row.
    """
    body = client.get("/health").json()

    assert body["variants_max"] == settings.variants_max
    assert isinstance(body["variants_max"], int)
    assert set(body["credentials"]) == {
        "zernio",
        "azure_chat",
        "azure_image",
        "cloudflare_rendering",
        "firecrawl_search",
        "brave_search",
    }
