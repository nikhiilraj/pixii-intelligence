from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import Column
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


class TemplateKind(StrEnum):
    HOOK = "hook"
    STRUCTURE = "structure"
    VISUAL = "visual"


class TemplateStatus(StrEnum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    RETIRED = "retired"


class Template(SQLModel, table=True):
    """One version of one template.

    Editing never mutates a row — it writes a new one sharing the same ``family_id`` with
    an incremented ``version``. That is what lets performance attributed to an earlier
    version stay attached to the wording that actually earned it.

    All three kinds share this table with a JSON ``body`` whose shape depends on ``kind``:
    a hook carries a slot pattern, a structure carries ordered sections, a visual carries
    its renderer and either a component name or a prompt skeleton.
    ponytail: one table with a discriminator. Split only if a kind grows columns the
    others never use.
    """

    __tablename__ = "template"

    id: int | None = Field(default=None, primary_key=True)

    # Stable across every version. The thing performance is attributed to.
    family_id: str = Field(index=True)
    version: int = 1

    kind: TemplateKind = Field(index=True)
    name: str
    status: TemplateStatus = Field(default=TemplateStatus.PROPOSED, index=True)

    body: dict = Field(default_factory=dict, sa_column=Column(JSONB))
    slots: list[dict] = Field(default_factory=list, sa_column=Column(JSONB))

    # Zernio post ids this template was derived from, so an abstraction can always be
    # checked back against the posts that justify it.
    provenance: list[str] = Field(default_factory=list, sa_column=Column(JSONB))

    notes: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
