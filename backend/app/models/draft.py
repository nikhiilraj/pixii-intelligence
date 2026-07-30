from datetime import UTC, datetime

from sqlalchemy import Column, LargeBinary
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


class Draft(SQLModel, table=True):
    """A generated post, stamped at creation with what produced it.

    Lineage keys on **(family_id, version)** rather than a template row id, because editing
    a template writes a new row — keying on the id would silently detach this draft's
    history the first time anyone revised the template it used.

    Recording lineage is the whole point of the exercise: it is cheap now and impossible to
    reconstruct later, and it is what makes template performance answerable at all.
    """

    __tablename__ = "draft"

    id: int | None = Field(default=None, primary_key=True)

    idea: str = ""
    # "directed" (an operator supplied the idea) or "autonomous" (a scheduled run did).
    mode: str = Field(default="directed", index=True)

    hook_family: str = Field(index=True)
    hook_version: int = 1
    structure_family: str = Field(index=True)
    structure_version: int = 1
    visual_family: str | None = Field(default=None, index=True)
    visual_version: int | None = None

    hook_text: str = ""
    body_text: str = ""

    visual_values: dict = Field(default_factory=dict, sa_column=Column(JSONB))
    # The assets chosen for this draft's `image_url` slots, slot name -> asset id as text.
    #
    # A separate column from `visual_values` rather than more keys in it. One dict holding
    # two kinds of value — prose the model wrote and an asset id a human picked — cannot be
    # read back unambiguously: `"7"` in a `big_number` slot is a number and `"7"` in an
    # `image_url` slot is a reference to a file on disk, and the renderer, the delete guard
    # and the Zernio metadata all need to tell those apart.
    #
    # `nullable=False` where `visual_values` is nullable, deliberately: every reader merges
    # this dict, and a `None` on the rows that predate the column would make each of them
    # guard for it. The migration backfills `{}`.
    asset_values: dict = Field(
        default_factory=dict, sa_column=Column(JSONB, nullable=False)
    )
    visual_image: bytes | None = Field(default=None, sa_column=Column(LargeBinary))
    # Why the visual could not be produced. The words survive a failed image.
    visual_error: str | None = None

    # Set by the publishing slice. Absent means this draft never left the building.
    zernio_post_id: str | None = Field(default=None, index=True)
    pushed_at: datetime | None = None
    # When the pushed draft was observed live on the platform. NULL is the meaningful state:
    # pushed, but Monte has not published it yet.
    #
    # **Named `went_live_at`, not `published_at`.** `Post.published_at` already exists, and a
    # `select(Draft, Post)` merge carrying two identically-named columns resolves to whichever
    # one the query happens to pick — wrong silently, with no error anywhere. The different
    # name makes that bug impossible to write.
    #
    # Together with `pushed_at` this derives every state a draft can be in, which is why there
    # is no status column: a third source of truth could disagree with these two.
    went_live_at: datetime | None = None

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @property
    def full_text(self) -> str:
        return f"{self.hook_text}\n\n{self.body_text}".strip()
