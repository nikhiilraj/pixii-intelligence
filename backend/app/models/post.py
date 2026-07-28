from datetime import datetime
from enum import StrEnum

from sqlalchemy import Column
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


class PostSource(StrEnum):
    """Where a corpus row came from.

    Zernio rows are refreshed on every sync. Manual rows are external reference material —
    creator posts, pasted text — that no sync knows about and must never overwrite.
    """

    ZERNIO = "zernio"
    MANUAL = "manual"


class Post(SQLModel, table=True):
    """A post as Zernio knows it, with the metrics it reported.

    Rows arrive two ways: ingested from Zernio (historical, and anything written outside
    this app — those carry no lineage), or created here (those do). Lineage columns are
    added by a later slice; absence of lineage is a normal state, not a defect.
    """

    __tablename__ = "post"

    id: int | None = Field(default=None, primary_key=True)

    # Zernio's own identifier for the post. The natural key for upserts.
    zernio_id: str = Field(index=True, unique=True)
    late_post_id: str | None = None

    source: PostSource = Field(default=PostSource.ZERNIO, index=True)
    platform: str = Field(index=True)
    content: str = ""
    status: str = ""
    published_at: datetime | None = Field(default=None, index=True)
    scheduled_for: datetime | None = None

    platform_post_id: str | None = None
    platform_post_url: str | None = None
    account_username: str | None = None

    media_type: str | None = None
    thumbnail_url: str | None = None
    media_items: list[dict] = Field(default_factory=list, sa_column=Column(JSONB))

    # Filename of the locally cached media, relative to the media directory.
    # None means either the post has no media or the download failed — both survivable.
    local_media_path: str | None = None

    # True for posts Zernio synced from the platform rather than published itself.
    is_external: bool = False

    impressions: int = 0
    reach: int = 0
    likes: int = 0
    comments: int = 0
    shares: int = 0
    saves: int = 0
    clicks: int = 0
    views: int = 0
    follows: int = 0
    engagement_rate: float = 0.0

    # The primary success measure: a count, so a small-reach post cannot outrank a
    # large-reach one on efficiency alone. Stored rather than computed so the dashboard
    # can sort on it in the database.
    engaged_actions: int = Field(default=0, index=True)

    metrics_updated_at: datetime | None = None
