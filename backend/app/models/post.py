from datetime import datetime
from enum import StrEnum

from sqlalchemy import Column, Enum
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


class Verdict(StrEnum):
    """A human's ruling on a published post.

    The only form of learning that is honest at n=1. Engagement spans 12.7x across this
    corpus at ~3 samples per template, so no aggregate can rank anything yet — a verdict
    claims judgement, not statistics, and must never be read as a performance measure.
    """

    WORKED = "worked"
    DIDNT = "didnt"
    MIXED = "mixed"


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

    # Held out of template extraction. For posts that ranked high for reasons that cannot
    # repeat — a launch announcement, a network firing once — so the generator does not
    # learn a trick that only worked the first time. The post stays in the corpus.
    excluded_from_extraction: bool = False

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

    # A human's ruling on how the post actually landed, and why. NULL is the queue: a
    # published post nobody has judged yet.
    #
    # A non-native Enum over a plain VARCHAR, deliberately, and each half of that matters:
    #   - not a Postgres ENUM like `source` above, so a fourth verdict is an edit to
    #     `Verdict` rather than an ALTER TYPE migration (the migration says the same);
    #   - not a bare String, because Verdict is a StrEnum: `"worked" == Verdict.WORKED` is
    #     True but `"worked" is Verdict.WORKED` is False, so a column that read back as a
    #     bare str would hand every caller a value that fails an identity check. This type
    #     coerces on read, so a row always carries a real `Verdict`. Same hazard, same
    #     remedy as the cohort boundary at `extraction.py:196`.
    # `values_callable` persists the value (`worked`), not the member name (`WORKED`),
    # matching the migration's String column and what the API speaks.
    verdict: Verdict | None = Field(
        default=None,
        sa_column=Column(
            # VARCHAR(6) is DDL only, and the migration already made the real column an
            # unbounded String — a longer fourth verdict needs no schema change.
            Enum(
                Verdict,
                native_enum=False,
                create_constraint=False,
                values_callable=lambda enum: [member.value for member in enum],
            ),
            nullable=True,
        ),
    )

    # Why the human ruled that way. Empty is normal — a ruling without a reason is still a
    # ruling. The 500-character cap lives at the route; this column is unbounded.
    verdict_note: str = ""

    # When the ruling was recorded. Moves when a verdict is re-set, because the useful
    # question is when someone last judged this post, not when they first did.
    # ponytail: no verdict history table — one ruling per post is the real cardinality, and
    # a history has no reader until two people use this app.
    verdict_at: datetime | None = None
