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

    # The daily editorial slot. The hour is **local** to `daily_slot_timezone`, and the run
    # is keyed on the local date — see `daily.slot_date` for why a UTC date runs the slot
    # twice on some days and never on others.
    #
    # The tick is not the slot. It asks "is today's slot due and unclaimed", which is why a
    # process that was asleep at 09:00 still runs the day's slot when it wakes. Thirty
    # minutes is one indexed lookup on a table with one row per day; the interval bounds how
    # late a recovered run can be, not how much work is done.
    daily_slot_hour: int = 9
    daily_slot_timezone: str = "Asia/Kolkata"
    daily_tick_minutes: int = 30

    # Where a Teams card's link points. Not a credential and not a secret — it is the
    # address of this app's own web UI, which authorises on its own when the link opens.
    pixii_base_url: str = "http://localhost:3000"

    # The publishing kill switch. Off by default, and that default is the feature: the
    # schedule/publish/cancel code can ship and sit inert until someone deliberately turns
    # it on, and can be turned off again during an incident without a code rollback.
    #
    # It stops **external commands only**. Generation, review, and pushing a draft to Zernio
    # all continue — the switch exists so a publishing problem does not take the rest of the
    # tool down with it.
    #
    # This is the runtime half of the decision recorded in
    # `docs/adr/0002-human-publication-authority.md`: automation prepares, a human commands,
    # and on localhost with one operator that human is the authorisation boundary. The day
    # this runs anywhere else, authentication comes before this flag may be true.
    publishing_enabled: bool = False

    # Reconciliation: how long after a post's own moment — its scheduled instant, or the
    # moment a `publish_now` was accepted — before Zernio is asked what actually happened.
    #
    # A grace period rather than an immediate check, because a post that is one second past
    # its schedule and still `scheduled` is a queue doing its job, not a drift. Fifteen
    # minutes is comfortably beyond any publish latency and far inside the interval that
    # matters to a person.
    reconcile_grace_minutes: int = 15
    # How long an accepted command may sit with no clear answer before someone is told that
    # Pixii cannot confirm it. Longer than the metrics tick, so at least one check has
    # happened and come back unclear before the card goes out; short enough that the news
    # still arrives on the day. This does **not** end the polling — see `reconcile.py`.
    reconcile_stale_hours: int = 12

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

    # The Asset holding the real brand logo. Extraction pins it as the default for every
    # slot a proposal declares with `role: "logo"`, so the mark is embedded file bytes and
    # no model ever draws it.
    #
    # Not a credential — a row id — so no `repr=False` and no /health flag. Unset is the
    # normal first-run state: a logo slot simply carries no default and the picker asks.
    brand_logo_asset_id: int | None = None

    def configured(self) -> dict[str, bool]:
        """Which credentials are present, without revealing any value."""
        return {
            "zernio": bool(self.zernio_api_key),
            "azure_chat": bool(self.azure_openai_chat_api_key),
            "azure_image": bool(self.azure_openai_image_api_key),
            "cloudflare_rendering": bool(self.cloudflare_browser_rendering_token),
        }


settings = Settings()
