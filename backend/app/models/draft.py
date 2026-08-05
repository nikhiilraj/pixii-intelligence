from datetime import UTC, datetime

from sqlalchemy import Column, LargeBinary
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

from app.models.stage import GenerationStage


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

    # Editorial lineage. Nullable so drafts created before the Studio workflow was wired
    # remain readable; new directed generations fill all three applicable relationships.
    editorial_brief_id: int | None = Field(
        default=None, foreign_key="editorial_brief.id", index=True
    )
    angle_plan_id: int | None = Field(default=None, foreign_key="angle_plan.id", index=True)
    research_job_id: int | None = Field(
        default=None, foreign_key="research_job.id", index=True
    )
    correlation_id: str | None = Field(default=None, index=True)

    # Honest workflow/review state — `models/stage.GenerationStage`, and the predicates there
    # are the only place "may a human act on this" is decided.
    #
    # **The default is `unreviewed`, and it used to be `ready`.** A default of `ready` meant a
    # row nothing had reviewed was pushable the moment it was constructed, which is what made
    # variants, retopic, the autonomous run and legacy `POST /drafts` into four separate
    # bypasses of the review boundary. The reviewed workflow sets `planning` and moves from
    # there; anything that does not say where it came from is not vouched for.
    #
    # Still a plain `str` column and not an enum type: rows migrated to `ready` before this
    # existed have to stay readable, and `stage_of` answers `None` for a value it cannot name
    # rather than raising on read. Changing the Python default does not touch stored rows.
    generation_stage: str = Field(
        default=GenerationStage.UNREVIEWED, index=True, nullable=False
    )
    generation_error: str | None = None
    gate_results: list = Field(default_factory=list, sa_column=Column(JSONB, nullable=False))
    readiness_result: dict | None = Field(
        default=None, sa_column=Column(JSONB, nullable=True)
    )
    revision_rounds: int = Field(default=0, nullable=False)
    write_prompt_name: str | None = None
    write_prompt_version: str | None = None

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
    # The image the last redraw replaced, so "is this better than what I had" is answerable.
    #
    # ponytail: one level of undo, not a history table. Two images answer the question
    # actually being asked. Add a table when someone wants three.
    previous_visual: bytes | None = Field(default=None, sa_column=Column(LargeBinary))
    # Why the visual could not be produced. The words survive a failed image.
    visual_error: str | None = None

    # Set by the publishing slice. Absent means this draft never left the building.
    zernio_post_id: str | None = Field(default=None, index=True)
    # Where `visual_image` was uploaded, so a retry can reference the same file.
    #
    # **This column is a duplicate-post guard, not a cache.** Zernio rejects a repeat of the
    # same post within 24 hours by hashing `(platform, accountId, content + media URLs)` —
    # and every presign returns a freshly randomised URL. So a push that succeeded remotely
    # while its response was lost would, on retry, upload again, hash differently, and create
    # a *second* post in the account. Storing the URL is what reproduces the fingerprint and
    # lets Zernio's own dedup catch the retry. Written before the post is created, and
    # committed there rather than flushed, because the whole point is to outlive a request
    # that dies mid-create.
    #
    # Deliberately **not** added to `DraftOut`, unlike the columns whose omission that class
    # warns about: nothing in Studio reads or acts on this, and a storage URL for a file the
    # reviewer already sees rendered is not information the review screen needs.
    zernio_media_url: str | None = None
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

    # How many times a human-visible change has been made to what this draft would publish.
    #
    # **Means "what a person would publish changed", not "a column changed."** A publication
    # command carries the revision the reviewer was looking at, and a mismatch is refused —
    # so bumping this on the wrong write silently breaks publishing rather than protecting
    # it. `push_draft` writes `zernio_media_url` and `pushed_at` and must never bump it, or
    # every push would invalidate its own command. The bumps live in `_edited`, one place,
    # for the same reason.
    revision: int = Field(default=1, nullable=False)

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @property
    def full_text(self) -> str:
        return f"{self.hook_text}\n\n{self.body_text}".strip()

    def edited(self) -> None:
        """Record that what a human would publish has changed.

        A method rather than `draft.revision += 1` at each call site, so the answer to "what
        counts as an edit" lives in one place and can be read at once. Call it from anything
        that changes the words or the picture; do not call it from anything that only records
        where the post went.
        """
        self.revision += 1
