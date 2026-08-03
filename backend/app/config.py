from datetime import datetime
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Every credential below carries `Field(repr=False)`. `repr(settings)` printed live keys in full,
# and pytest dumps the repr of both operands on any failed assertion touching `settings.*` — so one
# unrelated test failure in CI would write real Cloudflare and Zernio keys into the run log.
# `configured()` was already careful never to reveal a value; the default repr went around it.
# `repr=False` rather than `SecretStr`: it redacts without changing the field type, so no call site
# has to learn `.get_secret_value()`. ponytail: if a value ever needs redacting in a log *message*
# as well as a repr, that is when SecretStr earns its migration.

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

    # Downloaded post media. Gitignored — it is a local cache of Zernio's files, not
    # source. ponytail: local disk; swap for object storage when this runs on more than
    # one machine.
    media_dir: Path = REPO_ROOT / "media"

    # Scheduled jobs run in the API process. Off by default under test so a suite never
    # starts a background thread that talks to Zernio.
    enable_scheduler: bool = False
    metrics_sync_hours: int = 6

    # Whose writing templates are allowed to describe. Extraction learns voice from this
    # account only — creator posts stay in the corpus as reference material, but a
    # template claiming to be Monte's voice must not be led by someone else's writing.
    # ponytail: one account string, and the account names differ per platform
    # (`Pixii_ai` on twitter, `pixii.creates` on youtube), so extraction on any platform
    # but LinkedIn now returns an empty sample. Make this a per-platform mapping when
    # X and Reddit land.
    voice_account: str = "Monte Desai"

    # Other creators' posts, collected as reference material. They are allowed to teach a
    # hook shape or a post structure — those are borrowable patterns — but never a voice.
    # See `extraction.Cohort` for the split and `generation._exemplars` for the guard.
    inspiration_account: str = "Creator inspiration"

    # How far back the current voice reaches. The corpus goes further back than this, but
    # the older posts are a different genre — AI-industry commentary rather than the
    # Amazon-listing work Pixii publishes now — and ranked on engagement alone they crowd
    # out the posts a template should describe. Cross-era engagement is not comparable
    # either: those posts reached a different audience and carry no impressions to
    # normalise against.
    voice_since: datetime = datetime(2025, 1, 1)

    # Below this many attributed posts, a template's aggregate is shown but marked
    # insufficient. Across this corpus a post's engagement spans 12.7x, so a handful of
    # samples cannot separate a good template from a lucky one.
    min_sample_size: int = 5

    # Autonomous runs. The cap is a hard ceiling on drafts per run so a scheduling fault
    # cannot flood the review queue. Off by default — unattended generation is opt-in.
    enable_autonomous: bool = False
    autonomous_max_drafts: int = 2
    autonomous_interval_hours: int = 24

    # How many drafts one idea may be written as at `POST /drafts/variants`. **A ceiling, not a
    # default**: N variants is N times the paid completions and N renders inside one request, and
    # `autonomous_max_drafts` was a default that a query parameter could walk straight past —
    # `?cap=500` bought up to 1001 billed completions. The route clamps to this with the same
    # `min(requested, ceiling)`, so a large N is impossible rather than discouraged.
    # 3 because that is what a person can hold side by side and choose between; it is not a
    # claim that three is the right number of experiments.
    variants_max: int = 3
    # A webhook URL is a bearer credential in URL clothing — anyone holding it can post.
    teams_webhook_url: str = Field(default="", repr=False)

    zernio_api_key: str = Field(default="", repr=False)
    zernio_base_url: str = "https://getlate.dev/api/v1"
    getlate_linkedin_id: str = ""

    azure_openai_chat_endpoint: str = ""
    azure_openai_chat_api_key: str = Field(default="", repr=False)
    azure_openai_chat_deployment: str = ""
    azure_openai_chat_api_version: str = ""

    azure_openai_image_endpoint: str = ""
    azure_openai_image_api_key: str = Field(default="", repr=False)
    azure_openai_image_deployment: str = ""
    azure_openai_image_api_version: str = ""

    cloudflare_account_id: str = ""
    cloudflare_browser_rendering_token: str = Field(default="", repr=False)

    def configured(self) -> dict[str, bool]:
        """Which credentials are present, without revealing any value."""
        return {
            "zernio": bool(self.zernio_api_key),
            "azure_chat": bool(self.azure_openai_chat_api_key),
            "azure_image": bool(self.azure_openai_image_api_key),
            "cloudflare_rendering": bool(self.cloudflare_browser_rendering_token),
        }


settings = Settings()
