from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# .env lives at the repo root, one level above backend/. It is a symlink to the
# vault's .env so credentials have a single source of truth.
REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Configuration read from the environment.

    Every external credential is named here so the required surface is greppable in
    one place. Values never appear in source, logs, or committed files.
    """

    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = "postgresql+psycopg://pixii:pixii@localhost:5433/pixii_intelligence"

    zernio_api_key: str = ""
    zernio_base_url: str = "https://getlate.dev/api/v1"
    getlate_linkedin_id: str = ""

    azure_openai_chat_endpoint: str = ""
    azure_openai_chat_api_key: str = ""
    azure_openai_chat_deployment: str = ""
    azure_openai_chat_api_version: str = ""

    azure_openai_image_endpoint: str = ""
    azure_openai_image_api_key: str = ""
    azure_openai_image_deployment: str = ""
    azure_openai_image_api_version: str = ""

    cloudflare_account_id: str = ""
    cloudflare_browser_rendering_token: str = ""

    def configured(self) -> dict[str, bool]:
        """Which credentials are present, without revealing any value."""
        return {
            "zernio": bool(self.zernio_api_key),
            "azure_chat": bool(self.azure_openai_chat_api_key),
            "azure_image": bool(self.azure_openai_image_api_key),
            "cloudflare_rendering": bool(self.cloudflare_browser_rendering_token),
        }


settings = Settings()
