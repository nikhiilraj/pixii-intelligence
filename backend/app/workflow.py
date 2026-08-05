"""Running a Studio workflow where someone can watch it, and only ever once.

`POST /drafts/workflow` used to run the whole pipeline inside the request and commit at the
end. The stages were written — `planning`, `researching`, `drafting` — but only ever flushed,
so no other connection could read one: for the minute or more a run takes, the browser had a
spinner and the database had nothing. A reload lost the attempt entirely, because there was no
row to come back to.

Three pieces close that, and they are only separable on paper:

- **A claim**, so a double-click cannot buy two minutes of paid calls (`claim_key`).
- **A worker**, with its own `Session`, committing each stage as it enters it (`run`).
- **A sweep**, so a run whose process died stops reading as one still going (`sweep_stalled`).

ponytail: a thread and a column, not a task queue. One operator, one process, and a run that
is lost when the process dies is a run the sweep can declare dead and Retry can start again.
The ceiling is a real broker the day a second process serves this API — at which point the
claim column is already the thing that stops both of them running the same request.
"""

import logging
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.dialects.postgresql import insert
from sqlmodel import Session, col, select

from app.config import settings
from app.db import engine, utc
from app.generation import generate_reviewed_draft, new_reviewed_draft
from app.llm import LLM, AzureChat
from app.models.draft import Draft
from app.models.stage import IN_FLIGHT, GenerationStage
from app.rendering import AzureImageRenderer, CloudflareRenderer, HtmlRenderer, ImageRenderer
from app.research import SearchProvider, search_provider

log = logging.getLogger("pixii.workflow")


class Superseded(Exception):
    """Something else ended this run while it was still working, and it must stop.

    Raised by the worker's checkpoint, never caught by it. The only thing that ends a run
    from outside is `sweep_stalled`, and a swept run has already been reported to a reviewer
    as failed — so the worker finishing afterwards and committing `ready` over the top would
    make the screen contradict itself, and would resurrect a draft a person has already
    retried. The sweep wins; this is how the worker finds out.
    """


def claim_key(
    *,
    idea: str,
    hook_id: int | None,
    structure_id: int | None,
    visual_id: int | None,
    asset_values: dict[str, str],
    research_mode: str | None,
) -> str:
    """A stable name for one workflow request. Derived, never generated.

    `distribution.idempotency_key`'s reason, at a different boundary: two submissions of the
    same request have to produce the same string, or the second press of Generate is a second
    minute of billed calls. A key minted per request — a uuid4, a timestamp — would be unique
    by construction and would therefore guard nothing at all.

    **Derived from the request, and deliberately not from a draft id.** Retry creates a new
    row and then starts a run for it; keying on that row's id would give the second click of
    Retry a key nothing holds, and buy the second run this exists to refuse.

    `uuid5` over a namespaced string, as `publishing` and `distribution` both do, so this
    cannot collide with an id minted anywhere else in the system. The asset picks are part of
    it because they reach the rendered image: the same idea through the same templates with a
    different logo is a different request, not a repeat of the last one.
    """
    assets = ",".join(f"{name}={value}" for name, value in sorted(asset_values.items()))
    name = (
        f"pixii-intelligence/workflow/{idea}/{hook_id}/{structure_id}/{visual_id}"
        f"/{assets}/{research_mode or '-'}"
    )
    return str(uuid.uuid5(uuid.NAMESPACE_URL, name))


def claim(session: Session, llm: LLM, *, key: str, **lineage: Any) -> tuple[Draft, bool]:
    """The draft this run owns, and whether this call is the one that started it.

    `INSERT … ON CONFLICT DO NOTHING` against `UNIQUE (workflow_key)`, which is
    `distribution._record`'s pattern and `daily._claim`'s, for the reason both of them give:
    a read-then-insert is two statements and therefore two chances to interleave, and a
    double-click is precisely the input that interleaves them. Losing the insert is how the
    second press finds out, and it hands back the first press's draft rather than an error —
    the operator pressed Generate and a generation is running; that is not a failure.

    **The claim is carried by the draft's own insert, not by a placeholder claimed first.**
    `hook_family` and `structure_family` are NOT NULL, so a row cannot exist before its
    lineage is settled — and a row that could would be a draft, visible to `GET /drafts`,
    naming no templates. The cost is that a genuine double-click resolves templates twice
    before one of them loses; that is one `suggest_templates` call, against the minute of
    research, writing, verification and rendering this refuses to buy twice.

    Columns are read off the model rather than listed here, so a column added to `Draft`
    tomorrow reaches this insert without anybody remembering to add it — the hand-mapping
    trap `DraftOut` warns about, in the one other place the same list would appear.
    """
    # Twice, never more. The second attempt exists for one interleaving: this call loses the
    # insert to a run that then finishes and releases the claim before the lookup below, so
    # there is neither a winner nor a holder. Retrying resolves it; retrying forever would
    # spend a completion per attempt chasing a race that has already been lost twice.
    for _ in range(2):
        draft = new_reviewed_draft(session, llm, workflow_key=key, **lineage)
        values = {name: getattr(draft, name) for name in Draft.model_fields if name != "id"}
        statement = (
            insert(Draft)
            .values(**values)
            .on_conflict_do_nothing(index_elements=["workflow_key"])
            .returning(col(Draft.id))
        )
        inserted = session.execute(statement).scalar_one_or_none()
        # Committed before the thread starts, and that is the whole point of the arrangement:
        # the worker opens its own connection, so a draft that is only flushed here is a draft
        # the worker cannot see. `daily._claim` commits for the same reason — a claim visible
        # to nobody holds nothing.
        session.commit()
        if inserted is not None:
            started = session.get(Draft, inserted)
            assert started is not None
            return started, True
        held = session.exec(select(Draft).where(col(Draft.workflow_key) == key)).first()
        if held is not None:
            return held, False
    raise RuntimeError(f"could not claim workflow {key}: it was taken and released twice")


@dataclass(frozen=True)
class Resources:
    """The four adapters a run needs, built for the run rather than for the request.

    **The request's own dependencies cannot be used here and this is not a preference.**
    `SessionDep`, `LLMDep`, `SearchDep` and both renderers are generator dependencies: FastAPI
    closes every one of them when the response is sent, which is before the worker has done
    anything at all. Reusing them is a use-after-close, and the session is the worst of the
    four because it fails by silently doing nothing rather than by raising.
    """

    llm: LLM
    search: Any
    html_renderer: HtmlRenderer
    image_renderer: ImageRenderer


@contextmanager
def resources() -> Iterator[Resources]:
    """Construct the four adapters and close them all, whatever happened in between.

    Mirrors `deps.get_llm`/`get_search`/`get_html_renderer`/`get_image_renderer` — the same
    constructors and the same close — because a background run must talk to exactly what a
    request talks to. `ExitStack` would generalise this; four named lines say what they are.
    """
    llm = AzureChat()
    search: SearchProvider = search_provider()
    html = CloudflareRenderer()
    image = AzureImageRenderer()
    try:
        yield Resources(llm=llm, search=search, html_renderer=html, image_renderer=image)
    finally:
        for adapter in (llm, search, html, image):
            close = getattr(adapter, "close", None)
            if close:
                close()


def _checkpoint(draft_id: int, key: str) -> Any:
    """A commit that refuses to write for a run that no longer owns its draft.

    The interleaving this exists for: a run slower than `WORKFLOW_TIMEOUT` is swept to
    `failed` and its claim released, a reviewer reads that, and then the run finishes and
    commits `ready` over the top. The screen would have said two different true-looking things
    about one draft, and the second would have undone a failure someone had already acted on.

    `SELECT … FOR UPDATE` **before** the flush, under `no_autoflush`, is what makes the check
    mean anything. Reading after the flush would read this session's own pending value; the
    lock taken before it is held until the commit below, so the sweep cannot land in between.
    """

    def checkpoint(session: Session) -> None:
        with session.no_autoflush:
            held = session.exec(
                select(Draft.workflow_key).where(col(Draft.id) == draft_id).with_for_update()
            ).first()
        if held != key:
            raise Superseded(f"draft {draft_id} is no longer claimed by this run")
        session.commit()

    return checkpoint


def run(
    draft_id: int,
    *,
    resources_: Resources,
    session_factory: Any = None,
    requested_mode: str | None = None,
) -> None:
    """Run the pipeline for an already-claimed draft, committing each stage as it enters it.

    Synchronous and importable, so every state transition, every failure and every trace row
    is testable by calling this — no thread, no sleep, no timing. `start` is the only thing
    that adds a thread, and it adds nothing else.

    **Its own `Session`, from its own connection.** That is the whole point: a stage committed
    on the request's session would still be invisible, because the request's session is closed
    by the time this runs.
    """
    factory = session_factory or (lambda: Session(engine))
    with factory() as session:
        draft = session.get(Draft, draft_id)
        if draft is None:  # deleted between the claim and the thread getting scheduled
            return
        checkpoint = _checkpoint(draft_id, draft.workflow_key or "")
        idea = draft.idea
        try:
            generate_reviewed_draft(
                session,
                resources_.llm,
                resources_.search,
                resources_.html_renderer,
                resources_.image_renderer,
                idea=idea,
                requested_mode=requested_mode,
                draft=draft,
                checkpoint=checkpoint,
            )
        except Superseded:
            # Falls through to the shared stop below without recording anything. The sweep's
            # reason is the true one; replacing it with this run's would be the resurrection
            # in a quieter costume.
            pass
        except Exception as exc:
            # Deliberately broad, and this is the handler the trace coupling rests on. An
            # error `generate_reviewed_draft` does not itself catch — anything raised outside
            # its four guarded stages — must still end as a *stored* failure carrying its
            # `GenerationTrace` rows, because a rollback here would erase the only record of
            # the calls this run paid for. The draft is the record; the traceback is not.
            log.exception("workflow %s failed", draft_id)
            draft.generation_stage = GenerationStage.FAILED
            draft.generation_error = f"{type(exc).__name__}: {exc}"
            _release(draft)
            session.add(draft)
        else:
            _release(draft)
            session.add(draft)
        try:
            checkpoint(session)
        except Superseded as stop:
            # Nothing is written. A swept run reaches here two ways — this check, or `advance`
            # refusing to move the terminal stage the sweep wrote — and both must end the same
            # way, because the failure above would otherwise be *this* run's message replacing
            # the sweep's honest one about a run that stopped responding. Its uncommitted trace
            # rows go too: a run that lost its claim is not one anybody is reading the record
            # of, and the alternative is resurrecting a draft a reviewer has already retried.
            session.rollback()
            log.warning("workflow %s stopped: %s", draft_id, stop)


def _release(draft: Draft) -> None:
    """Give up the claim, so the same request can be made again.

    Not `draft.edited()`: releasing a claim changes nothing a person would publish, and
    bumping the revision here would invalidate a publication command confirmed against the
    words this run just wrote.
    """
    draft.workflow_key = None


def start(
    draft_id: int, *, requested_mode: str | None = None, spawn: Any = None
) -> None:
    """Hand the run to a daemon thread and return.

    `daemon=True` so a shutdown is not held open by a run nobody is waiting for. What that
    costs is a run killed mid-flight, and the cost is bounded on purpose: every stage before
    the kill is already committed, and `sweep_stalled` turns the row it left behind into a
    stated failure a reviewer can retry from.

    `spawn` is the seam the route's tests use. Defaulted rather than injected everywhere, so
    the production path reads as one call and a test cannot accidentally start a real thread
    against the shared engine.
    """
    launch = spawn or _spawn
    launch(draft_id, requested_mode)


def _spawn(draft_id: int, requested_mode: str | None) -> None:
    def work() -> None:
        with resources() as adapters:
            run(draft_id, resources_=adapters, requested_mode=requested_mode)

    threading.Thread(target=work, name=f"workflow-{draft_id}", daemon=True).start()


def sweep_stalled(session: Session, *, now: datetime | None = None) -> list[Draft]:
    """Report every run that has stopped moving as failed, and release its claim.

    **On read, from `GET /drafts` and `GET /drafts/{id}`.** No scheduler and no tick: the only
    party who cares that a run is dead is the one polling it, and they are calling this route
    every second anyway. A background sweeper would be a second moving part to run, to
    configure and to notice had stopped — for a check that costs one indexed query against a
    handful of in-flight rows. `daily.run_daily_slot` buries its own stale rows on the same
    principle, from the tick that was going to run anyway.

    It writes rather than merely reporting, and the difference matters three ways: `retryable`
    reads the stored stage, so a run only *displayed* as failed could never be retried; the
    claim has to be released or that request is refused forever; and a reviewer who reads
    "failed" and comes back tomorrow must not find it claiming to be researching again.

    Aged from `created_at` — no new column. The only rows this can reach are in-flight, and an
    in-flight row is one run's own lifetime, so its age *is* the run's age. Through `db.utc`
    because `created_at` reads back naive once the row has round-tripped and subtracting a
    naive from an aware datetime raises.
    """
    at = now or datetime.now(UTC)
    cutoff = timedelta(minutes=settings.workflow_timeout_minutes)
    stalled = [
        draft
        for draft in session.exec(
            select(Draft).where(col(Draft.generation_stage).in_(sorted(IN_FLIGHT)))
        ).all()
        if at - utc(draft.created_at) >= cutoff
    ]
    for draft in stalled:
        # The stage it died in is in the message, because it is the only diagnosis available:
        # nothing recorded why the process went away, and "researching" versus "rendering"
        # is the difference between a search provider hanging and a renderer doing it.
        draft.generation_error = (
            f"the run stopped responding at {draft.generation_stage} and was declared failed "
            f"after {settings.workflow_timeout_minutes} minutes. Nothing is known about what "
            f"happened to it; Retry starts a fresh attempt and keeps this one."
        )
        draft.generation_stage = GenerationStage.FAILED
        _release(draft)
        session.add(draft)
    if stalled:
        # Committed here rather than left to the route. A `GET` that commits is unusual enough
        # to say out loud: this is not the request's own work, it is a correction to a row that
        # is lying, and leaving it uncommitted would make every reader re-derive it forever.
        session.commit()
    return stalled
