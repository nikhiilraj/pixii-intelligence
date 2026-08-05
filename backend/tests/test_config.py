"""Credentials must not survive into a repr.

`repr(settings)` used to print every key in full. That matters because pytest dumps the repr of
both operands on any failed assertion touching `settings.*`, and CI runs pytest — so one
unrelated failure would have written live Cloudflare and Zernio keys into a retained run log.

These tests deliberately assert against a **fabricated** secret rather than the real one. A test
that compared against `settings.zernio_api_key` would print the live value into the failure
output the moment it broke, which is the exact leak it exists to prevent.
"""

from app.config import Settings

# Recognisable, and not a real credential shape for any provider here.
CANARY = "canary-not-a-real-key-3f9a2c"

SECRET_FIELDS = (
    "zernio_api_key",
    "azure_openai_chat_api_key",
    "azure_openai_image_api_key",
    "cloudflare_browser_rendering_token",
    "teams_webhook_url",
    "firecrawl_api_key",
    "brave_search_api_key",
)


def _settings_with_canaries() -> Settings:
    return Settings(**{field: CANARY for field in SECRET_FIELDS})  # type: ignore[arg-type]


def test_no_credential_value_appears_in_the_repr():
    """The leak itself. One assertion per field so a failure names which one regressed."""
    rendered = repr(_settings_with_canaries())
    for field in SECRET_FIELDS:
        assert CANARY not in rendered, f"{field} leaked its value into repr(Settings)"


def test_no_credential_field_name_appears_in_the_repr_either():
    """`repr=False` removes the whole entry, so absence of the name is the stronger check.

    Guards the mistake of "fixing" this by masking the value while leaving the field present —
    a partially masked key still tells an attacker the shape and prefix.
    """
    rendered = repr(_settings_with_canaries())
    for field in SECRET_FIELDS:
        assert field not in rendered


def test_non_secret_configuration_is_still_visible():
    """Redaction has to stop at credentials.

    `repr` is a debugging tool; blanket-hiding configuration would trade one bad failure mode
    for another, where a wrong database or media path is invisible in the dump that would
    otherwise explain the failure.
    """
    rendered = repr(_settings_with_canaries())
    assert "voice_account" in rendered
    assert "min_sample_size" in rendered
    assert "zernio_base_url" in rendered


def test_the_values_are_still_readable_by_code():
    """Redaction must not have become removal — the app has to be able to authenticate."""
    settings = _settings_with_canaries()
    for field in SECRET_FIELDS:
        assert getattr(settings, field) == CANARY


def test_configured_reports_presence_without_revealing_anything():
    """`configured()` is what the Inbox footer renders, so it must stay boolean-only."""
    reported = _settings_with_canaries().configured()
    assert reported == {
        "zernio": True,
        "azure_chat": True,
        "firecrawl_search": True,
        "azure_image": True,
        "cloudflare_rendering": True,
        "brave_search": True,
    }
    assert CANARY not in repr(reported)
