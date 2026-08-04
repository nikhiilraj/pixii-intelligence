from datetime import UTC, datetime

from sqlmodel import Field, SQLModel

# What a human asked Pixii to do to a post that already exists in Zernio.
#
# `save_remote_draft` is deliberately absent: `push_draft` already does that, has its own
# idempotency story, and predates this table. Adding a second path to the same effect would
# give one action two records that could disagree.
SCHEDULE = "schedule"
PUBLISH_NOW = "publish_now"
CANCEL_SCHEDULE = "cancel_schedule"
ACTIONS = (SCHEDULE, PUBLISH_NOW, CANCEL_SCHEDULE)

# `requested` — written down, not yet sent. `accepted` — Zernio took it. `failed` — Zernio
# refused, or the attempts ran out. Nothing here claims the post is live; that is
# `Draft.went_live_at`, observed rather than assumed.
REQUESTED = "requested"
ACCEPTED = "accepted"
FAILED = "failed"


class Publication(SQLModel, table=True):
    """One human command to schedule, publish, or cancel — and what became of it.

    This row is the audit record. It names the action, the exact draft revision the reviewer
    was looking at, the time in all three forms it exists in, the outcome, and the reason for
    a refusal.

    ponytail: no separate `audit_event` table and no `publication_attempt` table. With one
    operator on localhost, an `actor` column that always holds the same person is ceremony,
    and one command's history is a counter and a string. Both arrive the day there are two
    people — which is the same day authentication does.
    """

    __tablename__ = "publication"

    id: int | None = Field(default=None, primary_key=True)

    draft_id: int = Field(foreign_key="draft.id", index=True)

    # The revision the reviewer confirmed against. Stored rather than looked up, because the
    # whole point is to be able to say later *which* version of the words went out — and a
    # draft edited afterwards would answer that question wrongly.
    draft_revision: int

    action: str = Field(index=True)

    # The three forms of one instant, all kept.
    #
    # Every datetime column in this schema is `timestamp without time zone`, so a single
    # column could not carry the zone even if we wanted it to. That is not the only reason
    # for three: "09:00 on the 12th, India time" and "03:30 UTC on the 12th" are the same
    # instant but not the same intent, and a reviewer confirming a schedule needs to see the
    # words they typed, not a conversion of them. If the zone's offset changes between the
    # command and the run, the recorded local time is what says what was meant.
    #
    # NULL for `publish_now` and `cancel_schedule`, which name no future time. Nullable, not
    # zeroed: no time was requested, rather than a time of midnight.
    requested_local_time: datetime | None = None
    timezone: str | None = None
    scheduled_utc: datetime | None = None

    # Derived from (draft, revision, action, resolved time) and unique, so the same command
    # submitted twice is one row and one external effect. The uniqueness is the guarantee —
    # a check-then-insert would race with itself.
    #
    # Its own key, not `push_draft`'s. That one keys on the draft id alone and works together
    # with the `zernio_post_id` guard and Zernio's 24-hour content hash; reusing it here
    # would entangle two dedup schemes that are correct separately.
    idempotency_key: str = Field(unique=True, index=True)

    state: str = Field(default=REQUESTED, index=True)
    attempts: int = Field(default=0)
    # Zernio's own words for a refusal, so the operator sees why rather than "it failed".
    last_error: str | None = None

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    # When Zernio accepted it. NULL while `requested`, and NULL forever if it failed —
    # never a placeholder, because "accepted at midnight" is a thing that did not happen.
    accepted_at: datetime | None = None
