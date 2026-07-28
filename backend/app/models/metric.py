from datetime import UTC, datetime

from sqlmodel import Field, SQLModel


class MetricSnapshot(SQLModel, table=True):
    """One reading of a post's numbers at a moment in time.

    Snapshots accumulate rather than overwrite: engagement arrives over hours and days, and
    the curve is the interesting part. The Post row keeps the latest values for cheap
    sorting; this table keeps the history.
    """

    __tablename__ = "metric_snapshot"

    id: int | None = Field(default=None, primary_key=True)
    post_id: int = Field(foreign_key="post.id", index=True)
    captured_at: datetime = Field(default_factory=lambda: datetime.now(UTC), index=True)

    impressions: int = 0
    reach: int = 0
    likes: int = 0
    comments: int = 0
    shares: int = 0
    saves: int = 0
    clicks: int = 0
    views: int = 0
    engagement_rate: float = 0.0

    # The primary success measure, stored so history can be charted without recomputing.
    engaged_actions: int = 0
