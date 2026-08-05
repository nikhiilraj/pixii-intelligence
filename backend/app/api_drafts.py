import base64
from datetime import datetime

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel
from sqlmodel import col, desc, select

from app import editorial, research, workflow
from app.api_research import _out as research_out
from app.autonomous import AutonomousRunFailed, SpendMeter, run_autonomous
from app.config import settings
from app.deps import HtmlRendererDep, ImageRendererDep, LLMDep, SearchDep, SessionDep, ZernioDep
from app.distribution import (
    CommandRefused,
    NotPushed,
    NotReviewReady,
    PublishingDisabled,
    RevisionDrift,
    StaleRevision,
    submit,
)
from app.generation import (
    NoUsableTemplates,
    generate_draft,
    generate_reviewed_draft,
    generated_from,
    regenerate_text,
    regenerate_visual,
    retopic,
    suggest_templates,
    variant_combinations,
)
from app.llm import LLMResponseError
from app.metrics import draft_for_post
from app.models.draft import Draft
from app.models.editorial import AnglePlan, EditorialBrief, PlannedClaim
from app.models.generation_trace import GenerationTrace
from app.models.post import Post
from app.models.publication import CANCEL_SCHEDULE, PUBLISH_NOW, SCHEDULE, Publication
from app.models.stage import GenerationStage, retryable, review_ready
from app.models.template import Template
from app.notify import notify
from app.publishing import PushFailed, push_draft

router = APIRouter(prefix="/drafts", tags=["drafts"])


class IdeaIn(BaseModel):
    idea: str
    hook_id: int | None = None
    structure_id: int | None = None
    visual_id: int | None = None
    # Slot name -> asset id, for the visual's `image_url` slots. Ignored for any other slot
    # (`generation.chosen_assets`), so this is not a second route to writing prose into a
    # template. Absent slots fall back to the slot's own `default_asset_id`.
    asset_values: dict[str, str] = {}
    # The operator's research depth, or `None` for "none was expressed" — which is not `"none"`
    # mode. `None` lets the detected floor decide; `"none"` is a request to look nothing up, and
    # is refused for a brief whose floor is above it. Passed straight to `research.resolve_mode`
    # and never compared here; see `_refuse_below_floor`.
    research_mode: str | None = None


class DraftOut(BaseModel):
    """A draft plus the lineage, spelled out so review shows what produced it.

    **Hand-mapped, and every field has to be added in two places** — here and in `_out`.
    Nothing derives this from the model, so a new `Draft` column arrives with a green
    migration, a green query and a green test while the frontend sees nothing at all.
    `asset_values` (US-009), `went_live_at` (US-011) and now `pushed_at`/`created_at`
    (US-013) each had to be added by hand for that reason;
    `test_a_new_draft_column_reaches_the_api` is what keeps the next one from being missed.
    """

    id: int
    idea: str
    mode: str
    hook_text: str
    body_text: str
    full_text: str
    visual_values: dict
    asset_values: dict
    visual_error: str | None
    visual_png: str | None
    # Whether there is a previous image to show or restore. The bytes themselves are not
    # inlined here the way `visual_png` is — `GET /{id}/previous-visual` fetches those,
    # since every list/get caller pays for this field but few will ever want the picture.
    has_previous_visual: bool
    zernio_post_id: str | None
    # When Zernio accepted the push. Together with `created_at` this is what gives the Inbox
    # an age per queue: "built 6 days ago and never pushed" is the thing a count cannot say,
    # and an age is the only signal that distinguishes a queue from a stalled one.
    pushed_at: datetime | None
    # Null means pushed but not yet live. With `zernio_post_id` this is what lets the Inbox
    # tell "awaiting Monte" from "published" without a status column.
    went_live_at: datetime | None
    created_at: datetime
    # What a publication command must be confirmed against. Without it on the wire the
    # review screen has nothing to submit and the stale-revision guard cannot fire.
    revision: int
    # Which revision Zernio is holding, or null for a draft that never left. On the wire for
    # the same reason `revision` is: the panel cannot say "Zernio holds revision 1, this draft
    # is revision 3" — and cannot disable the three controls when they differ — from a number
    # it does not have. Without it the drift is only ever discovered by attempting a command.
    pushed_revision: int | None
    lineage: dict
    generation_stage: str
    generation_error: str | None
    gate_results: list[dict]
    readiness_result: dict | None
    # Which claim, or which words of the operator's idea, stands behind each assertion the
    # post makes. Null means this draft was never verified — a historical row, or one that
    # stopped before verification — which is not the same as one that asserted nothing.
    verification_result: dict | None
    revision_rounds: int
    editorial: dict | None


def _lineage(session: SessionDep, draft: Draft) -> dict:
    """Names for the exact versions this draft was generated from."""

    def named(family: str | None, version: int | None) -> dict | None:
        if not family:
            return None
        template = session.exec(
            select(Template).where(Template.family_id == family, Template.version == version)
        ).first()
        return {
            "family": family,
            "version": version,
            "name": template.name if template else "(version no longer present)",
        }

    return {
        "hook": named(draft.hook_family, draft.hook_version),
        "structure": named(draft.structure_family, draft.structure_version),
        "visual": named(draft.visual_family, draft.visual_version),
    }


def _out(session: SessionDep, draft: Draft) -> DraftOut:
    return DraftOut(
        id=draft.id or 0,
        idea=draft.idea,
        mode=draft.mode,
        hook_text=draft.hook_text,
        body_text=draft.body_text,
        full_text=draft.full_text,
        visual_values=draft.visual_values,
        asset_values=draft.asset_values,
        visual_error=draft.visual_error,
        visual_png=(base64.b64encode(draft.visual_image).decode() if draft.visual_image else None),
        has_previous_visual=draft.previous_visual is not None,
        zernio_post_id=draft.zernio_post_id,
        pushed_at=draft.pushed_at,
        went_live_at=draft.went_live_at,
        created_at=draft.created_at,
        revision=draft.revision,
        pushed_revision=draft.pushed_revision,
        lineage=_lineage(session, draft),
        generation_stage=draft.generation_stage,
        generation_error=draft.generation_error,
        gate_results=list(draft.gate_results),
        readiness_result=draft.readiness_result,
        verification_result=draft.verification_result,
        revision_rounds=draft.revision_rounds,
        editorial=_editorial_lineage(session, draft),
    )


def _editorial_lineage(session: SessionDep, draft: Draft) -> dict | None:
    """The persisted artifacts behind this draft, or null for a historical row."""
    if draft.editorial_brief_id is None or draft.angle_plan_id is None:
        return None
    brief = session.get(EditorialBrief, draft.editorial_brief_id)
    plan = session.get(AnglePlan, draft.angle_plan_id)
    if brief is None or plan is None:
        return None
    claims = list(
        session.exec(
            select(PlannedClaim)
            .where(PlannedClaim.plan_id == plan.id)
            .order_by(col(PlannedClaim.id))
        ).all()
    )
    dossier = None
    research_error = None
    if draft.research_job_id is not None:
        try:
            dossier = research_out(research.dossier(session, draft.research_job_id)).model_dump()
        except Exception as exc:  # historical/failed job: lineage still renders
            research_error = f"{type(exc).__name__}: {exc}"
    return {
        "brief": {
            "id": brief.id,
            "objective": brief.objective,
            "audience": brief.audience,
            "desired_action": brief.desired_action,
            "constraints": brief.constraints,
            "requested_mode": brief.requested_mode,
            "recommended_mode": brief.recommended_mode,
            "research_mode": brief.research_mode,
            "mode_signals": brief.mode_signals,
            "prompt": {"name": brief.prompt_name, "version": brief.prompt_version},
        },
        "angle": {
            "id": plan.id,
            "thesis": plan.thesis,
            "tension": plan.tension,
            "audience_stake": plan.audience_stake,
            "cta": plan.cta,
            "beats": plan.beats,
            "prompt": {"name": plan.prompt_name, "version": plan.prompt_version},
        },
        "planned_claims": [{"id": claim.id, "text": claim.text} for claim in claims],
        "research_job_id": draft.research_job_id,
        "research": dossier,
        "research_error": research_error,
        "write_prompt": {
            "name": draft.write_prompt_name,
            "version": draft.write_prompt_version,
        },
        "prompts": _prompts_run(session, draft.correlation_id),
        "correlation_id": draft.correlation_id,
    }


def _prompts_run(session: SessionDep, correlation_id: str | None) -> list[dict]:
    """Every prompt that ran under this draft's correlation id, in call order.

    **The only way research and revision reach a review screen.** Four stages carry their own
    prompt on the artifact they wrote — the brief, the angle plan, the write and the rubric —
    and two do not: `research._QUERIES`/`_CLAIMS` write a `ResearchJob`, which has no prompt
    columns, and `revision._REVISION` writes nothing but a count. So a reviewer asking "which
    revision prompt rewrote this" had no answer anywhere, while the row that knows sat in
    `generation_trace` with no route reading it.

    Distinct `(name, version)` pairs with a count, not one entry per call: a revision loop makes
    up to three calls against one prompt, and three identical rows say nothing the count does
    not. Ordered by first call, which is stage order — and by nothing else. It is not a ranking
    and `calls` is not a score.

    A prompt whose version changed mid-run appears twice, and that is correct rather than
    untidy: two versions genuinely ran.

    ponytail: one query per draft with lineage, on a route that already reads a whole dossier
    per draft. Ceiling: a join, or dropping this from the list route, if `GET /drafts` is ever
    slow enough to measure.
    """
    if correlation_id is None:
        return []
    rows = session.exec(
        select(GenerationTrace)
        .where(GenerationTrace.correlation_id == correlation_id)
        .order_by(col(GenerationTrace.id))
    ).all()
    seen: dict[tuple[str, str], dict] = {}
    for row in rows:
        key = (row.prompt_name, row.prompt_version)
        if key not in seen:
            seen[key] = {"name": row.prompt_name, "version": row.prompt_version, "calls": 0}
        seen[key]["calls"] += 1
    return list(seen.values())


def _check_mode(idea: str, requested: str | None) -> None:
    """Refuse a depth below this idea's floor here, before anything is written.

    **Not a second opinion.** It calls `research.resolve_mode` on
    `editorial.research_question(idea)` — the same function, on the same text, that
    `editorial.build_brief` calls a moment later — so this cannot disagree with the run. The
    answer is thrown away; only the refusal is wanted.

    It is here because of what happens without it, which is *not* a 502. `build_brief` resolves
    the mode before it calls a model, and `generate_reviewed_draft` stores any planning error as
    a `failed` draft — so a below-floor request costs nothing and leaves a row per attempt
    saying `planning: ModeBelowFloor: …`. One press of Write variants leaves `variants_max` of
    them. Those rows are a user's typo recorded as failed generation attempts, and the reason is
    knowable before a draft exists; a refusal a caller can act on is worth more here than an
    audit record of a request that was never run.

    **The floor can still rise after this passes, and that failure stays a stored one.**
    `plan_angle` re-resolves over the idea *and* the claims the model just planned, which is a
    fact this request could not have known — see that docstring. So Studio has to render a
    `failed` draft whose error names `ModeBelowFloor` as well as reading this response; both
    paths exist and neither replaces the other.

    409 rather than 422: the same register as `NoUsableTemplates` above — the request is
    well-formed and the system will not serve it in the state it is in — and 422 is the shape
    FastAPI owns for its own validation errors, which clients parse differently.
    """
    try:
        research.resolve_mode(editorial.research_question(idea), requested)
    except research.ModeBelowFloor as exc:
        raise HTTPException(
            status_code=409,
            # Structured, not merely the sentence. A reviewer's first question about a
            # surprising floor is what the detector saw, and `signals` is the only answer;
            # composing it out of the message would mean parsing prose on the client.
            detail={
                "error": str(exc),
                "requested_mode": exc.requested,
                "recommended_mode": exc.recommended,
                "mode_signals": list(exc.signals),
            },
        ) from exc
    except ValueError as exc:
        # A mode name this version does not know — `resolve_mode`'s other refusal. 422 is right
        # here and 409 is not: the field's value is wrong, rather than the request conflicting
        # with anything about the idea.
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _load(session: SessionDep, draft_id: int) -> Draft:
    draft = session.get(Draft, draft_id)
    if draft is None:
        raise HTTPException(status_code=404, detail=f"no draft {draft_id}")
    return draft


def _renderer(session: SessionDep, draft: Draft, html_renderer, image_renderer):
    """Whichever renderer the draft's own visual version declares.

    `(family_id, version)`, not the newest version of the family: the redraw renders the
    version the draft's lineage names, so reading `renderer` off a newer row would hand an
    HTML template to the image renderer — or the reverse — as soon as one edit changes it.
    Still tolerant of a missing row, because `regenerate_visual` is what refuses that case.

    The generation-time twin is `generation._renderer_for`, which lives over there because it
    cannot be answered here: generation may suggest the visual, so the row is not settled
    until it has. Deliberately not merged with this one — see that docstring.
    """
    template = session.exec(
        select(Template).where(
            Template.family_id == draft.visual_family,
            Template.version == draft.visual_version,
        )
    ).first()
    declared = template.body.get("renderer") if template else "html"
    return image_renderer if declared == "ai" else html_renderer


@router.get("")
def list_drafts(session: SessionDep, limit: int = 100) -> list[DraftOut]:
    workflow.sweep_stalled(session)
    statement = select(Draft).order_by(desc(col(Draft.created_at))).limit(limit)
    return [_out(session, d) for d in session.exec(statement).all()]


@router.get("/{draft_id}")
def get_draft(session: SessionDep, draft_id: int) -> DraftOut:
    """One draft, with any run that has stopped moving reported as failed before it is read.

    The sweep is here rather than in a scheduled job because this is the route that cares:
    Studio polls it once a second for the whole of a run, so a dead run is noticed by the
    only party waiting on it, at the moment they ask, with nothing to deploy or keep alive.
    See `workflow.sweep_stalled` for why it writes rather than only reporting.
    """
    workflow.sweep_stalled(session)
    return _out(session, _load(session, draft_id))


@router.post("/suggest")
def suggest(session: SessionDep, llm: LLMDep, payload: IdeaIn) -> dict:
    """Which templates suit this idea, and why. Every choice is overridable."""
    try:
        suggestion = suggest_templates(session, llm, payload.idea)
    except NoUsableTemplates as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except LLMResponseError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {
        "hook": {"id": suggestion.hook.id, "name": suggestion.hook.name},
        "structure": {"id": suggestion.structure.id, "name": suggestion.structure.name},
        "visual": {"id": suggestion.visual.id, "name": suggestion.visual.name},
        "reason": suggestion.reason,
    }


@router.post("", status_code=201, deprecated=True)
def create_draft(
    session: SessionDep,
    llm: LLMDep,
    html_renderer: HtmlRendererDep,
    image_renderer: ImageRendererDep,
    payload: IdeaIn,
) -> DraftOut:
    """**Deprecated.** Direct generation with no review. Use `POST /drafts/workflow`.

    One completion, no brief, no angle, no research, no gates and no rubric. It is kept
    reachable because existing API clients call it, and marked `deprecated=True` so OpenAPI
    and `/docs` say so rather than leaving that fact in a comment.

    **What it produces is not pushable, and that is the arrangement rather than a bug.** It
    sets no stage, so `Draft.generation_stage` takes its default of `unreviewed`, and
    `review_ready` is false for that — so the push boundary refuses it and, since Stage C, so
    do the publication routes. Nothing here needs to enforce anything: the route writes words
    and declines to vouch for them, and every boundary downstream reads the same predicate.
    `test_review_boundary.py::test_the_legacy_create_route_produces_a_draft_that_cannot_be_
    pushed` is what keeps that true, and it is deliberately not weakened by this deprecation.

    It is no longer what `/drafts/variants` calls. That route ran the reviewed workflow as of
    Stage C, which is what makes this the last unreviewed creation path rather than one of
    four.

    Both renderers go down, and the choice is made below: this route may name no visual at
    all, in which case generation suggests one and only generation knows what it picked.
    """
    try:
        draft = generate_draft(
            session,
            llm,
            html_renderer,
            image_renderer,
            idea=payload.idea,
            hook_id=payload.hook_id,
            structure_id=payload.structure_id,
            visual_id=payload.visual_id,
            asset_values=payload.asset_values,
        )
    except NoUsableTemplates as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except LLMResponseError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    session.commit()
    session.refresh(draft)
    return _out(session, draft)


@router.post("/workflow", status_code=201)
def create_workflow_draft(session: SessionDep, llm: LLMDep, payload: IdeaIn) -> DraftOut:
    """Start the persisted editorial-to-review Studio workflow. Nothing publishes here.

    **Answers at `planning`, before the run has done anything.** It used to run the whole
    pipeline inside the request and commit at the end: a minute or more during which the
    stages were written but only flushed, so no other connection could read one — the browser
    had a spinner, the database had nothing, and a reload lost the attempt. Studio polls
    `GET /drafts/{id}` now, and every stage this answers with is a stage some other connection
    has already committed.

    No renderers and no search adapter are taken here, and that is not an omission: FastAPI
    closes a generator dependency when the response is sent, which is before the worker has
    started. `workflow.resources` builds the run its own. The LLM is taken because template
    resolution happens in this request — see `generation.new_reviewed_draft` for why a draft
    cannot exist before its lineage does.

    A second press returns the running draft rather than a second run. The claim is a UNIQUE
    index, so that holds against a double-click and not merely against a slow one.

    A depth below the idea's floor is refused before the claim, so nothing is written and no
    thread starts — see `_check_mode`, including what it deliberately cannot catch.
    """
    _check_mode(payload.idea, payload.research_mode)
    key = workflow.claim_key(
        idea=payload.idea,
        hook_id=payload.hook_id,
        structure_id=payload.structure_id,
        visual_id=payload.visual_id,
        asset_values=payload.asset_values,
        research_mode=payload.research_mode,
    )
    try:
        draft, started = workflow.claim(
            session,
            llm,
            key=key,
            idea=payload.idea,
            hook_id=payload.hook_id,
            structure_id=payload.structure_id,
            visual_id=payload.visual_id,
            asset_values=payload.asset_values,
        )
    except NoUsableTemplates as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if started:
        # After the commit inside `claim`, never before. A thread started against an
        # uncommitted row opens its own connection, finds no draft, and returns silently —
        # leaving a row nothing is working on and a Studio poll that never moves.
        workflow.start(draft.id or 0, requested_mode=payload.research_mode)
    return _out(session, draft)


@router.post("/{draft_id}/retry", status_code=201)
def retry_draft(session: SessionDep, llm: LLMDep, draft_id: int) -> DraftOut:
    """Retry a failed workflow as a new attempt, preserving the failed row for audit.

    `retryable`, not a hand-written pair of stage comparisons: both failures may be retried,
    an in-flight run may not — retrying one buys a second set of billed calls for a run that
    is still going — and a stage this version cannot name is not one it can restart.

    The new attempt starts in the background exactly as `POST /drafts/workflow` does, against
    the source's own recorded versions through `generated_from`. Not the newest version of
    each family: a retry reproduces the attempt that failed, and resolving forward would
    quietly retry something else.
    """
    source = _load(session, draft_id)
    if not retryable(source.generation_stage):
        raise HTTPException(status_code=409, detail="only a failed workflow can be retried")
    hook = generated_from(session, source.hook_family, source.hook_version)
    structure = generated_from(session, source.structure_family, source.structure_version)
    visual = generated_from(session, source.visual_family, source.visual_version)
    requested_mode = None
    if source.editorial_brief_id is not None:
        brief = session.get(EditorialBrief, source.editorial_brief_id)
        requested_mode = brief.requested_mode if brief else None
    key = workflow.claim_key(
        idea=source.idea,
        hook_id=hook.id,
        structure_id=structure.id,
        visual_id=visual.id,
        asset_values=source.asset_values,
        research_mode=requested_mode,
    )
    retried, started = workflow.claim(
        session,
        llm,
        key=key,
        idea=source.idea,
        hook_id=hook.id,
        structure_id=structure.id,
        visual_id=visual.id,
        asset_values=source.asset_values,
        mode=source.mode,
    )
    if started:
        workflow.start(retried.id or 0, requested_mode=requested_mode)
    return _out(session, retried)


class RetopicIn(BaseModel):
    """A new subject, plus exactly one source to inherit the templates from.

    Two optional ids rather than one required `source_id` with a `kind`: a caller holding a
    post and a caller holding a draft both have an integer, and a mistyped `kind` would send
    one of them looking up the wrong table and 404 for a row that exists.
    """

    idea: str
    source_draft_id: int | None = None
    source_post_id: int | None = None
    # The depth for the *new* subject, never inherited from the source — see `generation.retopic`,
    # which explains why a re-topic's floor is detected from the new idea. This is the operator
    # raising that floor deliberately, which is the one thing the detector cannot do for them.
    research_mode: str | None = None


class RetopicOut(DraftOut):
    """The new draft, plus what producing it cost. Subclassed, so `_out` stays the one
    mapping — the hand-mapping trap in `DraftOut` gets no second place to be missed."""

    llm_calls: int
    image_calls: int
    search_calls: int


def _source_draft(session: SessionDep, payload: RetopicIn) -> Draft:
    """The draft whose templates are being inherited, from either kind of id.

    A post resolves through `metrics.draft_for_post` — the join is
    `Draft.zernio_post_id == Post.late_post_id`, not `Post.zernio_id`, which is a different
    namespace and matches nothing. Most of the corpus was ingested rather than generated
    here, so "this post has no draft behind it" is the common case and gets its own answer.
    """
    if (payload.source_draft_id is None) == (payload.source_post_id is None):
        raise HTTPException(
            status_code=422,
            detail="give exactly one of source_draft_id or source_post_id",
        )
    if payload.source_draft_id is not None:
        return _load(session, payload.source_draft_id)

    post = session.get(Post, payload.source_post_id)
    if post is None:
        raise HTTPException(status_code=404, detail=f"no post {payload.source_post_id}")
    draft = draft_for_post(session, post)
    if draft is None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"post {post.id} was not generated here, so it records no templates to"
                " re-topic from"
            ),
        )
    return draft


@router.post("/retopic", status_code=201)
def create_retopic(
    session: SessionDep,
    llm: LLMDep,
    search: SearchDep,
    html_renderer: HtmlRendererDep,
    image_renderer: ImageRendererDep,
    payload: RetopicIn,
) -> RetopicOut:
    """A new draft on a new subject, through a past draft's exact templates.

    The source is read and never written: this is a new row, not `regenerate-text`, which
    rewrites one draft in place and holds its lineage still. Both the templates and the
    renderer come from the source's own `(family, version)` — see `generation.retopic`, which
    resolves the row and lets `generation._renderer_for` read the renderer off it — so a
    re-topic of a v1 draft is written and drawn by v1 even once v4 exists, and a version that
    has since been retired still works, because the version history is the attribution record.

    409 for a recorded version that is no longer in the table, exactly as the redraw does:
    the library cannot serve this draft's version, and silently substituting a sibling is
    the mis-attribution this route exists to avoid.

    **The new subject gets its own review.** The templates are inherited and the editorial work
    is not — a new brief, a new angle, its own planned claims and its own research floor. So
    what comes back may be `failed_review`, and that is a result rather than an error: a
    re-topic is not pushable because a model produced text against a template that once worked.
    The row is returned either way, with its stage and its reason, because a failed attempt
    that is auditable is worth more than a 502 that leaves nothing behind.

    `research_mode` raises that new floor and can never lower it, and a request below it is
    refused before the source is even resolved — see `_check_mode`.
    """
    _check_mode(payload.idea, payload.research_mode)
    source = _source_draft(session, payload)
    meter = SpendMeter()
    try:
        draft = retopic(
            session,
            meter.watch(llm),
            meter.watch(search),
            meter.watch(html_renderer),
            meter.watch(image_renderer),
            source,
            idea=payload.idea,
            requested_mode=payload.research_mode,
        )
    except NoUsableTemplates as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except LLMResponseError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    session.commit()
    session.refresh(draft)
    # Observed through the same meter the autonomous run uses, never predicted: a re-topic now
    # buys a whole workflow rather than one completion, and a call that failed bought itself
    # all the same.
    return RetopicOut(**_out(session, draft).model_dump(), **meter.spend())


class VariantsIn(BaseModel):
    """One idea, written N ways. `count` is a request, never a promise — see the route."""

    idea: str
    count: int | None = None
    # One depth for the whole batch. The idea is the same across every combination and the floor
    # is detected from the idea, so a per-variant depth would be N answers to one question.
    research_mode: str | None = None


class VariantsOut(BaseModel):
    """The batch, in generation order, and what the whole batch cost.

    **There is no fourth field, and that is the slice.** No score, no confidence, no
    recommendation, no ordering but the one the drafts were written in. Engagement spans 12.7x
    across ~3 samples per template here, so nothing this endpoint could add would mean anything;
    ranking is a threshold (~300 lineage-tagged posts, currently zero), not a feature.
    """

    variants: list[DraftOut]
    llm_calls: int
    image_calls: int
    # Web searches the batch bought. Real now that each variant runs the reviewed workflow —
    # see `SpendMeter.spend`, whose reason for omitting it was that no draft route could reach
    # one.
    search_calls: int


@router.post("/variants", status_code=201)
def create_variants(
    session: SessionDep,
    llm: LLMDep,
    search: SearchDep,
    html_renderer: HtmlRendererDep,
    image_renderer: ImageRendererDep,
    payload: VariantsIn,
) -> VariantsOut:
    """Write one idea as several drafts, each through a different approved combination.

    The honest form of template comparison while the corpus cannot support ranking: three
    concrete drafts a human chooses between is judgement, and judgement is available now, where
    an aggregate over ~3 samples per template is not available at all.

    **Each combination runs the complete reviewed workflow.** A variant used to be one
    completion, which made "keep this one" a choice between three drafts nothing had checked —
    and `keep_variant` then left the survivor sitting in a queue that promises reviewed work.
    Each now gets its own brief, angle, planned claims, research floor, gates, bounded revision
    and readiness evaluation, so a variant is keepable and pushable because it passed and not
    because a model returned text.

    A batch may therefore come back with some variants `failed_review` and some `ready`, and
    all of them are returned. That is the point rather than an inconsistency: a failed variant
    is a row with its stage and its findings on it, which is what makes the comparison a
    comparison. `keep_variant` deletes what is discarded, so nothing unreviewed accumulates.

    **`settings.variants_max` is the ceiling, not the default**, clamped with the same
    `min(requested, ceiling)` as the autonomous route above — and here the stakes are now
    considerably higher than they were: every variant is a whole workflow inside one
    synchronous request, several billed completions and a render each, plus web searches for
    any combination whose research floor is `light` or `deep`. An unclamped `count` was
    `?cap=500` with a JSON body instead of a query string; it would now be that times the
    length of the workflow. The response reports what the batch actually bought.

    Ignores whatever is selected in the picker on purpose: the point is to vary the templates,
    so pinning one would answer a question nobody asked here — `POST /drafts` is where a chosen
    combination is written. Assets come from each visual's own `default_asset_id`.
    """
    # Before the clamp and before a single combination is resolved. Without it a below-floor
    # request writes one `failed` draft per variant, all with the same planning error, and
    # `keep_variant` is then the only way to clear them.
    _check_mode(payload.idea, payload.research_mode)
    meter = SpendMeter()
    # ponytail: clamped silently rather than 422'd, matching the autonomous route — the ceiling
    # is configuration, and "you asked for more than is allowed" has no answer for the caller
    # beyond the batch it gets. The response says how many arrived.
    count = (
        min(payload.count, settings.variants_max)
        if payload.count is not None
        else settings.variants_max
    )
    try:
        drafts = [
            generate_reviewed_draft(
                session,
                meter.watch(llm),
                meter.watch(search),
                meter.watch(html_renderer),
                meter.watch(image_renderer),
                idea=payload.idea,
                hook_id=hook.id,
                structure_id=structure.id,
                visual_id=visual.id,
                requested_mode=payload.research_mode,
            )
            for hook, structure, visual in variant_combinations(session, count)
        ]
    except NoUsableTemplates as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except LLMResponseError as exc:
        # The drafts written before the raise roll back with the request; the money does not.
        # Same reading as the autonomous 502 — the spend is reported on the failure path too.
        raise HTTPException(status_code=502, detail={"error": str(exc), **meter.spend()}) from exc

    session.commit()
    for draft in drafts:
        session.refresh(draft)
    return VariantsOut(variants=[_out(session, d) for d in drafts], **meter.spend())


class KeepIn(BaseModel):
    """Which variant survives, and which rows go. Ids, because the client holds them already.

    No batch column on `Draft` and no `variant_of` foreign key: the batch exists for as long as
    one screen is open, and a column would be a schema change plus a migration to record
    something nothing reads afterwards.
    """

    keep_id: int
    discard_ids: list[int] = []


@router.post("/variants/keep")
def keep_variant(session: SessionDep, payload: KeepIn) -> DraftOut:
    """Keep one variant and delete the rest. Discarded means deleted, not left lying around.

    A rejected variant left in the table is not litter, it is a lie: Inbox queue 2 ("built,
    awaiting push") is a human work queue and one of the four gates the Inbox exists to keep
    honest, so two drafts nobody chose sitting in it make the queue overstate the work waiting
    by exactly the amount this route was used.

    **Everything is validated before anything is deleted.** Loading and deleting in one pass
    would leave the first two rows gone when the third id turns out to be unknown or already
    pushed — a partial discard inside a request that answered with an error, which is the orphan
    this route exists to prevent.
    """
    if payload.keep_id in payload.discard_ids:
        raise HTTPException(
            status_code=422, detail=f"draft {payload.keep_id} is both kept and discarded"
        )
    kept = _load(session, payload.keep_id)
    discards = [_load(session, i) for i in dict.fromkeys(payload.discard_ids)]

    # A draft that reached Zernio has something outside this system pointing back at it, and
    # deleting the row would leave that post unattributable — which is the lineage record this
    # whole app is for. Nothing is deleted, not even the ones that could have been.
    pushed = [str(d.id) for d in discards if d.zernio_post_id]
    if pushed:
        raise HTTPException(
            status_code=409,
            detail=f"draft(s) {', '.join(pushed)} are in Zernio and cannot be discarded",
        )

    for draft in discards:
        session.delete(draft)
    session.commit()
    session.refresh(kept)
    return _out(session, kept)


@router.post("/{draft_id}/regenerate-text")
def rewrite(session: SessionDep, llm: LLMDep, draft_id: int) -> DraftOut:
    """Rewrite the words against the same templates. Lineage does not move.

    **Rewriting a draft that is already in Zernio does not touch the remote post, and nothing
    auto-syncs it.** That is a decision rather than an omission, and the state it leaves is
    made legible instead of hidden: `revision` moves and `pushed_revision` does not, so
    `GET /drafts/{id}` reports both and the publication panel can say "Zernio holds revision
    N, this draft is revision M" without attempting a command to find out. Syncing here
    silently would mean a rewrite — an editing action — quietly changed what an audience-facing
    post says, which is the authority ADR 0002 reserves for an explicit human command.

    The two routes forward from that state:

    - **Re-review, then push again.** A draft with editorial lineage is set to
      `failed_review` below, so the complete flow has to be retried before it is pushable at
      all. `POST /drafts/{id}/retry` starts that attempt as a *new* row, preserving this one
      for audit, and the new row pushes cleanly because it has never been pushed.
    - **Publish from Pixii**, which re-sends the words: `distribution._payload` now carries
      `content`, and every command re-uploads the visual unconditionally.

    **The second route is currently unreachable for this draft, and that is a known gap.**
    `distribution.submit` refuses a drifted draft precisely because Zernio's acceptance of
    `content` on update is unverified, and `publishing.push_draft` returns early once
    `zernio_post_id` is set — so a second push sends nothing and correctly records nothing.
    A `ready` draft that is pushed and then *redrawn* (a visual change bumps the revision
    without changing the stage) therefore has no way back short of
    `push_draft(force=True)`, which no route exposes and which mints a second Zernio post.
    `test_an_already_pushed_draft_that_drifted_cannot_clear_it_by_pushing_again` pins the dead
    end rather than hiding it; the repair, when someone wants it, is an in-place `update_post`
    in `push_draft`.
    """
    draft = _load(session, draft_id)
    try:
        regenerate_text(session, llm, draft)
    except (NoUsableTemplates, LLMResponseError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    draft.edited()
    if draft.editorial_brief_id is not None:
        draft.generation_stage = GenerationStage.FAILED_REVIEW
        draft.generation_error = "text was regenerated and must pass the complete review flow again"
        draft.gate_results = []
        draft.readiness_result = None
        # Cleared for the same reason as the other two: a claim-to-source review describes
        # sentences that no longer exist, and leaving it would show a reviewer the evidence
        # behind wording nobody can read any more. NULL is right rather than `{}` — nothing
        # has verified these words yet, which is exactly what NULL means on this column.
        draft.verification_result = None
    session.commit()
    session.refresh(draft)
    return _out(session, draft)


@router.post("/{draft_id}/regenerate-visual")
def redraw(
    session: SessionDep,
    html_renderer: HtmlRendererDep,
    image_renderer: ImageRendererDep,
    draft_id: int,
) -> DraftOut:
    """Redraw the image from the values already written. The words are untouched.

    Renders the version the draft was generated from, even a retired one — see
    `generation.generated_from`. A recorded version that is no longer in the table is a 409,
    not a 500: it is the same "the library cannot serve this" conflict as generation's.
    """
    draft = _load(session, draft_id)
    previous = draft.visual_image
    try:
        regenerate_visual(session, draft, _renderer(session, draft, html_renderer, image_renderer))
    except NoUsableTemplates as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    # Only when the redraw produced something. `_draw_visual` records a failure by setting
    # `visual_image = None`; in that case keep serving the last working image and leave the
    # older comparison slot untouched. `visual_error` still records why the redraw failed.
    if draft.visual_image is not None:
        draft.previous_visual = previous
        draft.edited()
    else:
        # A failed redraw leaves the last working image in place, so what a human would
        # publish has not changed and the revision must not move. Bumping it here would
        # invalidate a reviewer's confirmed command over a picture that never changed.
        draft.visual_image = previous
    session.commit()
    session.refresh(draft)
    return _out(session, draft)


@router.get("/{draft_id}/previous-visual")
def previous_visual(session: SessionDep, draft_id: int) -> Response:
    """The image the last redraw replaced, for showing beside the current one."""
    draft = _load(session, draft_id)
    if draft.previous_visual is None:
        raise HTTPException(status_code=404, detail=f"draft {draft_id} has no previous visual")
    return Response(content=draft.previous_visual, media_type="image/png")


@router.post("/{draft_id}/restore-visual")
def restore_visual(session: SessionDep, draft_id: int) -> DraftOut:
    """Put the previous image back, keeping the one it replaces.

    A swap, not a rollback: restoring is itself undoable, so a mis-click costs nothing.
    """
    draft = _load(session, draft_id)
    if draft.previous_visual is None:
        raise HTTPException(status_code=409, detail=f"draft {draft_id} has no previous visual")
    draft.visual_image, draft.previous_visual = draft.previous_visual, draft.visual_image
    draft.visual_error = None
    # Same reason as the redraw path in `generation._draw_visual`: a stored upload URL
    # describes the image being swapped out, and `push_draft` would re-send it.
    draft.zernio_media_url = None
    draft.edited()
    session.add(draft)
    session.commit()
    session.refresh(draft)
    return _out(session, draft)


@router.post("/{draft_id}/push")
def push(session: SessionDep, zernio: ZernioDep, draft_id: int) -> DraftOut:
    """Create this draft in Zernio — as a draft. Nothing here publishes or schedules.

    Safe to call twice: an already-pushed draft returns unchanged rather than creating a
    second post, and the underlying request carries a stable idempotency key.
    """
    draft = _load(session, draft_id)
    if not review_ready(draft.generation_stage):
        raise HTTPException(
            status_code=409,
            detail=(
                f"draft is not review-ready ({draft.generation_stage}): {draft.generation_error}"
            ),
        )
    try:
        push_draft(session, draft, zernio)
    except PushFailed as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    session.commit()
    session.refresh(draft)
    return _out(session, draft)


class ScheduleIn(BaseModel):
    """A schedule, in the words the reviewer typed plus the place that gives them meaning.

    `revision` is not optional and has no default. It is the whole stale-command guard: a
    caller who may omit it can publish a version of the words nobody approved, and a default
    would be this endpoint quietly supplying the answer it is supposed to be checking.
    """

    revision: int
    # Naive on purpose — "09:00 on the 12th" as written, with `timezone` saying where. An
    # offset-bearing string here would let the caller and the zone disagree with no error.
    local_time: datetime
    timezone: str


class ConfirmIn(BaseModel):
    """Everything an action needs when it names no time."""

    revision: int


class PublicationOut(BaseModel):
    """What became of one command. Hand-mapped for the reason `DraftOut` explains."""

    id: int
    draft_id: int
    draft_revision: int
    action: str
    requested_local_time: datetime | None
    timezone: str | None
    scheduled_utc: datetime | None
    state: str
    attempts: int
    last_error: str | None
    created_at: datetime
    accepted_at: datetime | None


def _publication_out(publication: Publication) -> PublicationOut:
    return PublicationOut(
        id=publication.id or 0,
        draft_id=publication.draft_id,
        draft_revision=publication.draft_revision,
        action=publication.action,
        requested_local_time=publication.requested_local_time,
        timezone=publication.timezone,
        scheduled_utc=publication.scheduled_utc,
        state=publication.state,
        attempts=publication.attempts,
        last_error=publication.last_error,
        created_at=publication.created_at,
        accepted_at=publication.accepted_at,
    )


def _command(
    session: SessionDep,
    zernio: ZernioDep,
    draft_id: int,
    *,
    action: str,
    revision: int,
    local: datetime | None = None,
    timezone: str | None = None,
) -> PublicationOut:
    """Every publication command comes through here, so every guard is in one place.

    The status codes are the contract and each says something different:

    - **403** the kill switch is off. Not 503: nothing is broken, the capability is turned
      off deliberately and turning it on is a decision, not a retry.
    - **409** the draft is not in the state the command assumes. Four distinct cases now, and
      they are one status because the fix for all four is to look at the draft rather than to
      try again: it moved under the reviewer, it has never been pushed, it is not review-ready,
      or what Zernio holds is no longer what this draft says. Only the first carries a
      structured body (`current_revision`), because it is the only one a reload resolves — the
      other three are sentences naming what to do, and `PublishPanel.classify` reads the
      parsed detail rather than the message text for exactly that reason.
    - **422** the command itself does not describe a moment — a past time, a missing zone.
    - **502** Zernio refused. Its own words are passed through so the operator sees the
      reason and not a shrug. **Never retried here**: a validation refusal is not a timeout,
      and retrying one is how a rejected command becomes two rejected commands.
    """
    draft = _load(session, draft_id)
    try:
        publication = submit(
            session, draft, zernio, action=action, revision=revision, local=local, timezone=timezone
        )
    except PublishingDisabled as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except StaleRevision as exc:
        raise HTTPException(
            status_code=409, detail={"error": str(exc), "current_revision": exc.current}
        ) from exc
    except (NotReviewReady, RevisionDrift, NotPushed) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except CommandRefused as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return _publication_out(publication)


@router.post("/{draft_id}/schedule")
def schedule(
    session: SessionDep, zernio: ZernioDep, draft_id: int, body: ScheduleIn
) -> PublicationOut:
    """Ask Zernio to publish this draft at a stated local time.

    Sends `isDraft: false` alongside the time. A `scheduledFor` on its own leaves the post a
    draft holding a schedule it will never act on — see `ZernioClient.update_post`.
    """
    return _command(
        session,
        zernio,
        draft_id,
        action=SCHEDULE,
        revision=body.revision,
        local=body.local_time,
        timezone=body.timezone,
    )


@router.post("/{draft_id}/publish")
def publish(
    session: SessionDep, zernio: ZernioDep, draft_id: int, body: ConfirmIn
) -> PublicationOut:
    """Publish this draft now.

    The single most consequential thing this application can do, and the reason every guard
    in `_command` exists. It is reachable only when `PUBLISHING_ENABLED` is true, only with
    the revision the reviewer confirmed against, and only for a draft already pushed.
    """
    return _command(session, zernio, draft_id, action=PUBLISH_NOW, revision=body.revision)


@router.post("/{draft_id}/cancel-schedule")
def cancel_schedule(
    session: SessionDep, zernio: ZernioDep, draft_id: int, body: ConfirmIn
) -> PublicationOut:
    """Withdraw a schedule, putting the post back to a draft in Zernio.

    The words and the picture stay exactly where they are; only the appointment is removed.
    """
    return _command(session, zernio, draft_id, action=CANCEL_SCHEDULE, revision=body.revision)


@router.get("/{draft_id}/publications")
def publications(session: SessionDep, draft_id: int) -> list[PublicationOut]:
    """Every command issued against this draft, newest first.

    This list *is* the audit trail — see `models/publication.py` for why there is no separate
    table. A review screen showing a Publish button without showing what has already been
    commanded is how one post gets scheduled twice by two people looking at the same page.
    """
    _load(session, draft_id)
    rows = session.exec(
        select(Publication)
        .where(col(Publication.draft_id) == draft_id)
        .order_by(col(Publication.created_at).desc())
    ).all()
    return [_publication_out(row) for row in rows]


@router.post("/autonomous-run")
def autonomous_run(
    session: SessionDep,
    llm: LLMDep,
    search: SearchDep,
    html_renderer: HtmlRendererDep,
    image_renderer: ImageRendererDep,
    cap: int | None = None,
) -> dict:
    """Run unattended generation now, capped. Produces drafts here; pushes nothing.

    `settings.autonomous_max_drafts` is the ceiling, not merely the default: a `cap` above it
    is clamped down to it. `run_autonomous` promises its cap is hard, and this is the only
    entry point where the number arrives from a caller — unclamped, `?cap=500` bought 1001
    billed chat completions and 500 renders inside one request, 250x the configured limit,
    against no spend counter anywhere in this app.

    **`settings.enable_autonomous` deliberately does not gate this route, and that is not an
    oversight.** That flag has exactly one reader — `scheduler.py:76`, where it decides
    whether the unattended job is registered at all — and what it switches off is generation
    happening *without anyone asking*. This route is a person asking. Gating a deliberate
    click behind the scheduler's switch would put the operator's own button behind a setting
    that is `False` by default and describes a different thing; the control that matters here
    is the cap, which is enforced above.

    An image renderer is injected alongside the HTML one because a run names no templates:
    every visual it draws is suggested, so this route cannot know which renderer the run will
    need. It was given only the HTML one, which made an `ai` suggestion undrawable here and
    counted the resulting `UnsupportedRenderer` as a failed image service.

    A search adapter goes down too, because the run generates through the reviewed workflow
    now — the same provider a directed generation gets. `created` counts drafts that passed
    review; a topic whose workflow concluded against it is in `failed` and is still a row.
    """
    # The meter is created here rather than inside `run_autonomous` so the count survives the
    # raise: `AutonomousRunFailed` leaves the run with no result to read, and a failed run is
    # exactly the one whose spend the caller cannot otherwise see.
    meter = SpendMeter()
    try:
        result = run_autonomous(
            session,
            meter.watch(llm),
            meter.watch(search),
            meter.watch(html_renderer),
            meter.watch(image_renderer),
            # ponytail: clamped silently rather than rejected with a 422. The ceiling is
            # configuration, not part of this endpoint's contract, so "you asked for more
            # than is allowed" has no useful answer for the caller beyond the run it gets.
            cap=min(cap, settings.autonomous_max_drafts)
            if cap is not None
            else settings.autonomous_max_drafts,
            notify=notify,
        )
    except AutonomousRunFailed as exc:
        # Same key names as the success body, so a caller reads the spend one way on both
        # paths. Note the asymmetry this exposes and does not create: the drafts written
        # before the raise roll back with the request, the money does not.
        raise HTTPException(status_code=502, detail={"error": str(exc), **meter.spend()}) from exc
    session.commit()
    return {
        "created": result.created,
        "failed": result.failed,
        # A draft that was created and has no picture. Reported beside `created` rather than
        # left to whoever opens the queue: this endpoint's answer is the only thing a caller
        # sees, and "3 created" with the images missing is the failure US-009 came to close.
        "visuals_failed": result.visuals_failed,
        "topics": result.topics,
        # Observed, never derived from `cap` or `topics`: a topic that fails still bought its
        # completion, and a render that raised still bought its render.
        **meter.spend(),
    }
