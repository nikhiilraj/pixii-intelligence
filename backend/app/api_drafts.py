import base64
from datetime import datetime

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel
from sqlmodel import col, desc, select

from app import research
from app.api_research import _out as research_out
from app.autonomous import AutonomousRunFailed, SpendMeter, run_autonomous
from app.config import settings
from app.deps import HtmlRendererDep, ImageRendererDep, LLMDep, SearchDep, SessionDep, ZernioDep
from app.distribution import (
    CommandRefused,
    NotPushed,
    PublishingDisabled,
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
    lineage: dict
    generation_stage: str
    generation_error: str | None
    gate_results: list[dict]
    readiness_result: dict | None
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
        lineage=_lineage(session, draft),
        generation_stage=draft.generation_stage,
        generation_error=draft.generation_error,
        gate_results=list(draft.gate_results),
        readiness_result=draft.readiness_result,
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
        "correlation_id": draft.correlation_id,
    }


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
    """
    template = session.exec(
        select(Template).where(
            Template.family_id == draft.visual_family,
            Template.version == draft.visual_version,
        )
    ).first()
    declared = template.body.get("renderer") if template else "html"
    return image_renderer if declared == "ai" else html_renderer


def _renderer_for(visual: Template | None, html_renderer, image_renderer):
    """Which renderer a *chosen* visual declares — the generation-time twin of `_renderer`.

    `_renderer` above resolves the renderer from a draft's recorded `(family, version)`, which
    is the right question for a redraw and the wrong one here: nothing has been generated yet,
    so the template row in hand is the one that will be used.
    """
    return image_renderer if visual and visual.body.get("renderer") == "ai" else html_renderer


@router.get("")
def list_drafts(session: SessionDep, limit: int = 100) -> list[DraftOut]:
    statement = select(Draft).order_by(desc(col(Draft.created_at))).limit(limit)
    return [_out(session, d) for d in session.exec(statement).all()]


@router.get("/{draft_id}")
def get_draft(session: SessionDep, draft_id: int) -> DraftOut:
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


@router.post("", status_code=201)
def create_draft(
    session: SessionDep,
    llm: LLMDep,
    html_renderer: HtmlRendererDep,
    image_renderer: ImageRendererDep,
    payload: IdeaIn,
) -> DraftOut:
    """Legacy direct generation used by variants and existing API clients."""
    visual = session.get(Template, payload.visual_id) if payload.visual_id else None
    renderer = _renderer_for(visual, html_renderer, image_renderer)
    try:
        draft = generate_draft(
            session,
            llm,
            renderer,
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
def create_workflow_draft(
    session: SessionDep,
    llm: LLMDep,
    search: SearchDep,
    html_renderer: HtmlRendererDep,
    image_renderer: ImageRendererDep,
    payload: IdeaIn,
) -> DraftOut:
    """Run the persisted editorial-to-review Studio workflow. Nothing publishes here."""
    visual = session.get(Template, payload.visual_id) if payload.visual_id else None
    renderer = _renderer_for(visual, html_renderer, image_renderer)
    try:
        draft = generate_reviewed_draft(
            session,
            llm,
            search,
            renderer,
            idea=payload.idea,
            hook_id=payload.hook_id,
            structure_id=payload.structure_id,
            visual_id=payload.visual_id,
            asset_values=payload.asset_values,
            requested_mode=payload.research_mode,
        )
    except NoUsableTemplates as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    session.commit()
    session.refresh(draft)
    return _out(session, draft)


@router.post("/{draft_id}/retry", status_code=201)
def retry_draft(
    session: SessionDep,
    llm: LLMDep,
    search: SearchDep,
    html_renderer: HtmlRendererDep,
    image_renderer: ImageRendererDep,
    draft_id: int,
) -> DraftOut:
    """Retry a failed workflow as a new attempt, preserving the failed row for audit."""
    source = _load(session, draft_id)
    if not retryable(source.generation_stage):
        raise HTTPException(status_code=409, detail="only a failed workflow can be retried")
    hook = generated_from(session, source.hook_family, source.hook_version)
    structure = generated_from(session, source.structure_family, source.structure_version)
    visual = generated_from(session, source.visual_family, source.visual_version)
    renderer = _renderer_for(visual, html_renderer, image_renderer)
    requested_mode = None
    if source.editorial_brief_id is not None:
        brief = session.get(EditorialBrief, source.editorial_brief_id)
        requested_mode = brief.requested_mode if brief else None
    retried = generate_reviewed_draft(
        session,
        llm,
        search,
        renderer,
        idea=source.idea,
        hook_id=hook.id,
        structure_id=structure.id,
        visual_id=visual.id,
        asset_values=source.asset_values,
        requested_mode=requested_mode,
        mode=source.mode,
    )
    session.commit()
    session.refresh(retried)
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


class RetopicOut(DraftOut):
    """The new draft, plus what producing it cost. Subclassed, so `_out` stays the one
    mapping — the hand-mapping trap in `DraftOut` gets no second place to be missed."""

    llm_calls: int
    image_calls: int


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
    html_renderer: HtmlRendererDep,
    image_renderer: ImageRendererDep,
    payload: RetopicIn,
) -> RetopicOut:
    """A new draft on a new subject, through a past draft's exact templates.

    The source is read and never written: this is a new row, not `regenerate-text`, which
    rewrites one draft in place and holds its lineage still. Both the templates and the
    renderer come from the source's own `(family, version)` — see `generation.retopic` and
    `_renderer` — so a re-topic of a v1 draft is written by v1 even once v4 exists, and a
    version that has since been retired still works, because the version history is the
    attribution record.

    409 for a recorded version that is no longer in the table, exactly as the redraw does:
    the library cannot serve this draft's version, and silently substituting a sibling is
    the mis-attribution this route exists to avoid.
    """
    source = _source_draft(session, payload)
    meter = SpendMeter()
    try:
        draft = retopic(
            session,
            meter.watch(llm),
            meter.watch(_renderer(session, source, html_renderer, image_renderer)),
            source,
            idea=payload.idea,
        )
    except NoUsableTemplates as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except LLMResponseError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    session.commit()
    session.refresh(draft)
    # Observed through the same meter the autonomous run uses, never predicted: a re-topic
    # buys one completion and one render, and a render that failed into `visual_error`
    # bought its call all the same.
    return RetopicOut(**_out(session, draft).model_dump(), **meter.spend())


class VariantsIn(BaseModel):
    """One idea, written N ways. `count` is a request, never a promise — see the route."""

    idea: str
    count: int | None = None


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


@router.post("/variants", status_code=201)
def create_variants(
    session: SessionDep,
    llm: LLMDep,
    html_renderer: HtmlRendererDep,
    image_renderer: ImageRendererDep,
    payload: VariantsIn,
) -> VariantsOut:
    """Write one idea as several drafts, each through a different approved combination.

    The honest form of template comparison while the corpus cannot support ranking: three
    concrete drafts a human chooses between is judgement, and judgement is available now, where
    an aggregate over ~3 samples per template is not available at all.

    **`settings.variants_max` is the ceiling, not the default**, clamped with the same
    `min(requested, ceiling)` as the autonomous route above — and here the stakes are the same
    arithmetic: every variant is another billed completion and another render inside one
    synchronous request, so an unclamped `count` is `?cap=500` again with a JSON body instead of
    a query string.

    Ignores whatever is selected in the picker on purpose: the point is to vary the templates,
    so pinning one would answer a question nobody asked here — `POST /drafts` is where a chosen
    combination is written. Assets come from each visual's own `default_asset_id`.
    """
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
            generate_draft(
                session,
                meter.watch(llm),
                meter.watch(_renderer_for(visual, html_renderer, image_renderer)),
                idea=payload.idea,
                hook_id=hook.id,
                structure_id=structure.id,
                visual_id=visual.id,
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
    """Rewrite the words against the same templates. Lineage does not move."""
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
    - **409** the draft moved under the reviewer, or has never been pushed. Both are "the
      thing you are commanding is not in the state you think", and both are fixed by looking
      again rather than by trying again.
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
    except NotPushed as exc:
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
    html_renderer: HtmlRendererDep,
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
    """
    # The meter is created here rather than inside `run_autonomous` so the count survives the
    # raise: `AutonomousRunFailed` leaves the run with no result to read, and a failed run is
    # exactly the one whose spend the caller cannot otherwise see.
    meter = SpendMeter()
    try:
        result = run_autonomous(
            session,
            meter.watch(llm),
            meter.watch(html_renderer),
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
