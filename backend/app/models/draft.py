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
    visual_image: bytes | None = Field(default=None, sa_column=Column(LargeBinary))
    # Why the visual could not be produced. The words survive a failed image.
    visual_error: str | None = None

    # Set by the publishing slice. Absent means this draft never left the building.
    zernio_post_id: str | None = Field(default=None, index=True)
    pushed_at: datetime | None = None

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @property
    def full_text(self) -> str:
        return f"{self.hook_text}\n\n{self.body_text}".strip()
