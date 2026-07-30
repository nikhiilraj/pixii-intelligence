from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import Column
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


class AssetKind(StrEnum):
    """What an asset is, so a picker can narrow to the right shelf.

    A flat vocabulary rather than a tree: these are the five kinds a post visual draws
    from, and a kind is a filter, not a permission.
    """

    LOGO = "logo"
    PRODUCT = "product"
    SCREENSHOT = "screenshot"
    BRAND = "brand"
    PHOTO = "photo"


class Asset(SQLModel, table=True):
    """One reusable image, stored on local disk and named by its own content.

    Rows arrive two ways: uploaded by hand, or promoted out of the corpus by a later
    slice — those carry ``source_post_id``, so an asset can always be traced back to the
    post it came from. Absence of a source is the normal state for an upload.

    Dimensions are the dimensions of the file *on disk*, not of whatever was uploaded: an
    oversized image is downscaled before it is stored, and recording the pre-scale size
    would misdescribe the bytes a renderer is about to embed.
    """

    __tablename__ = "asset"

    id: int | None = Field(default=None, primary_key=True)

    # Basename of the stored file, relative to `assets.assets_dir()`. Served by the
    # existing /media mount at /media/assets/{filename}.
    filename: str

    # What a human calls this. Defaults to the uploaded file's stem.
    label: str = ""

    kind: AssetKind = Field(index=True)
    tags: list[str] = Field(default_factory=list, sa_column=Column(JSONB))

    width: int
    height: int

    # Digest of the bytes as uploaded, before any downscaling. The natural key for
    # "this is the same file again", which is a question about what was handed to us,
    # not about what re-encoding happened to produce.
    sha256: str = Field(index=True, unique=True)

    # The corpus post this was lifted from, for assets promoted rather than uploaded.
    source_post_id: int | None = Field(default=None, foreign_key="post.id", index=True)

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
