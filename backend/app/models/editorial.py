"""The two artifacts that stand between an idea and a draft: a brief, and the plan of what
the post will argue.

Three tables, and the split between them is the argument. A brief says what the post is
*for*; a plan says what it will *assert*. The assertions are rows — one claim per row, with
an id — because the next slice has to map each of them to a citation, and a paragraph of
prose has nothing to map. "This claim was never sourced" must be a state you can `WHERE` on,
which is the same shape `claim`/`citation` already take one stage downstream.

**Research depth lives on the brief and is never decided here.** `research.resolve_mode` owns
that policy in full; these columns record what it answered, including the floor it applied, so
that "the floor said light and the run did none" stays an answerable question rather than an
inference. `research_job` stores the pair for the same reason and says so at length.
"""

from datetime import UTC, datetime

from sqlalchemy import Column
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

# The one channel there is. Named rather than assumed, like `daily_run.DAILY_SLOT`, so that a
# second channel is a value someone passes and not a migration.
#
# ponytail: no channel table, no per-channel length or format rules. Ceiling: the day a brief
# can be written for somewhere other than LinkedIn, the rules that differ per channel are what
# needs a home — not the name, which is already a column.
CHANNEL = "linkedin"


class EditorialBrief(SQLModel, table=True):
    """What one post is for, before anything has been written.

    Every field here is either the operator's own words or the model's answer about them.
    Nothing on this row is a measurement, which is why the only nullable columns are the two
    that mean "nobody said" — see each of them.
    """

    __tablename__ = "editorial_brief"

    id: int | None = Field(default=None, primary_key=True)

    # Groups every model call made while producing this brief and its plan, and is the same id
    # `generation_trace` rows carry. A brief, its plan and the calls that wrote them are one
    # operation.
    correlation_id: str = Field(index=True)

    # The operator's idea, verbatim. First-party text: it is what the whole brief is derived
    # from, and it is the one field on this row no model wrote.
    idea: str

    objective: str
    audience: str
    channel: str = Field(default=CHANNEL)
    # The one thing a reader could do having read it. Singular on purpose — a list of desired
    # actions is a post with no call to action.
    desired_action: str

    # Limits on the writing, as sentences. `nullable=False` with a `[]` default, following
    # `research_job.mode_signals`: a brief with no constraints is a real answer and an empty
    # list is how it reads. NULL would say the brief pass never ran, and it did.
    constraints: list = Field(default_factory=list, sa_column=Column(JSONB, nullable=False))

    # --- research depth, as `research.resolve_mode` answered it ------------------------------
    #
    # **NULL means the operator expressed no preference**, which is a different thing from
    # asking for `none`. Defaulting this to `"none"` would state a request nobody made, and it
    # would be a loud lie rather than a quiet one: `resolve_mode` raises `ModeBelowFloor` for a
    # `none` request against any brief that leans on the outside world, so every factual brief
    # would start failing at the door.
    requested_mode: str | None = None
    # What the floor detector said this brief needs, and what the run will actually use. Never
    # below it — `resolve_mode` raises rather than clamping, so a row where these differ always
    # differs upwards.
    recommended_mode: str
    research_mode: str = Field(index=True)
    # Why the floor came out where it did. `[]` is the honest answer for a brief that tripped
    # nothing, so this is not nullable.
    mode_signals: list = Field(default_factory=list, sa_column=Column(JSONB, nullable=False))

    # When the operator proposes to publish. **NULL is "nobody proposed a time"** — never
    # `now()`, which would be the moment the brief was written wearing a publication plan's
    # name. Nothing schedules from this column; it is recorded because a brief that named a
    # date and a brief that named none are different briefs.
    proposed_time: datetime | None = None

    # --- lineage ------------------------------------------------------------------------------
    #
    # The exact prompt that wrote this row. Stored here as well as on the `generation_trace`
    # rows the same call produced, and that is not duplication: a correlation id covers both
    # stages and every retry inside them, so answering "which prompt wrote *this* objective"
    # from traces alone means picking a row out of a set by name and hoping. The artifact
    # carries its own provenance, which is the `(family_id, version)` rule applied one layer up.
    prompt_name: str
    prompt_version: str

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class AnglePlan(SQLModel, table=True):
    """One thesis, the tension under it, and how the post gets from one to the other.

    A plan belongs to a brief and there may be more than one: re-planning writes a new row
    rather than editing this one, so a draft written from the first plan still points at the
    words it was written from. Same reason editing a template writes a new version.
    """

    __tablename__ = "angle_plan"

    id: int | None = Field(default=None, primary_key=True)
    brief_id: int = Field(foreign_key="editorial_brief.id", index=True)
    correlation_id: str = Field(index=True)

    # One thesis, as a column and not a list. **There is deliberately no second angle here and
    # no field that could hold one**, because a set of angles invites an ordering and an
    # ordering of angles is a ranking — the rule this project keeps for templates, and it
    # transfers exactly: nothing in this application has the evidence to say one angle beats
    # another. A different angle is a different plan row.
    thesis: str
    # What is in dispute — the reason the thesis is worth stating rather than assumed.
    tension: str
    # Why this audience loses or gains something by it.
    audience_stake: str
    cta: str

    # The narrative beats in order, as sentences. Order is the model's, and it is the only
    # ordering on this row that means anything; nothing else here is sorted.
    beats: list = Field(default_factory=list, sa_column=Column(JSONB, nullable=False))

    # Subjects this post may not repeat. **The caller's list, stored verbatim, never the
    # model's echo of it.** Asking the model to hand the list back makes a dropped entry
    # indistinguishable from a subject it decided was fine to cover again, and the failure
    # would be a duplicate post nobody could explain. `[]` means the caller named none.
    must_not_repeat: list = Field(default_factory=list, sa_column=Column(JSONB, nullable=False))

    prompt_name: str
    prompt_version: str

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class PlannedClaim(SQLModel, table=True):
    """One thing the post intends to assert, addressable on its own.

    A row per claim, rather than a list on the plan, for the reason `claim` is a row one stage
    later: the next slice has to say "this claim is supported by that citation", and that
    sentence needs an id on both sides. It is also what makes an unsourced claim a query
    instead of something a reader has to notice.

    There is no `verifiable` column and that is deliberate. Whether a claim needs a citation is
    a judgement `research`'s floor detector makes over a *brief* — several sentences of context
    — and it has a known blind spot on a single sentence: a capitalised word at the start of a
    sentence is read as the start of a sentence, so "Microsoft shipped a new tier" trips no
    signal on its own. A boolean written from that would be believed by the gate that reads it,
    and it would under-cite. `editorial.research_question` puts every claim on a bulleted line
    where that blind spot does not apply, and asks the detector once, about the whole plan.
    """

    __tablename__ = "planned_claim"

    id: int | None = Field(default=None, primary_key=True)
    plan_id: int = Field(foreign_key="angle_plan.id", index=True)

    # One statement. Bounded when it is written — see `editorial.CLAIM_MAX_CHARS`; a paragraph
    # accepted here is a claim plan that has quietly become prose again.
    text: str

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
