from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.db import get_session
from app.main import app


@pytest.fixture
def api(session) -> Iterator[TestClient]:
    app.dependency_overrides[get_session] = lambda: session
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_it_reports_the_account_the_push_actually_sends(api, monkeypatch):
    """`publishing.push_draft` sends `settings.getlate_linkedin_id` as the accountId.

    The confirmation screen has to name that, not something adjacent to it.
    """
    monkeypatch.setattr(settings, "getlate_linkedin_id", "acct-real")

    body = api.get("/publishing").json()

    assert body["account_id"] == "acct-real"
    assert body["platform"] == "linkedin"


def test_the_voice_account_is_not_the_destination(api, monkeypatch):
    """The conflation this endpoint exists to make impossible.

    `voice_account` names whose writing templates may describe. It is a human-readable name,
    which is what makes substituting it tempting, and it is not where anything is published.
    The two can differ with nothing anywhere reporting it.
    """
    monkeypatch.setattr(settings, "voice_account", "Monte Desai")
    monkeypatch.setattr(settings, "getlate_linkedin_id", "acct-real")

    assert "Monte Desai" not in api.get("/publishing").text


def test_an_unset_account_is_absent_and_not_blank(api, monkeypatch):
    """None, not "". A blank renders as a name nobody typed; absence renders as `—`."""
    monkeypatch.setattr(settings, "getlate_linkedin_id", "")

    assert api.get("/publishing").json()["account_id"] is None


def test_it_says_whether_publishing_is_allowed(api, monkeypatch):
    """Otherwise the only way to learn the capability is off is to attempt the thing."""
    monkeypatch.setattr(settings, "publishing_enabled", False)
    assert api.get("/publishing").json()["enabled"] is False

    monkeypatch.setattr(settings, "publishing_enabled", True)
    assert api.get("/publishing").json()["enabled"] is True


def test_no_credential_is_reported(api, monkeypatch):
    """The bearer key is the credential; the account id is the address."""
    monkeypatch.setattr(settings, "zernio_api_key", "sk-must-not-appear")

    assert "sk-must-not-appear" not in api.get("/publishing").text
