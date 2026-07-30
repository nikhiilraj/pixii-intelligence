import base64

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlmodel import col, desc, select

from app.autonomous import AutonomousRunFailed, run_autonomous
from app.config import settings
from app.deps import HtmlRendererDep, ImageRendererDep, LLMDep, SessionDep, ZernioDep
from app.generation import (
    NoUsableTemplates,
    generate_draft,
    regenerate_text,
    regenerate_visual,
    suggest_templates,
)
from app.llm import LLMResponseError
from app.models.draft import Draft
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


class DraftOut(BaseModel):
    """A draft plus the lineage, spelled out so review shows what produced it.

    **Hand-mapped, and every field has to be added in two places** — here and in `_out`.
    Nothing derives this from the model, so a new `Draft` column arrives with a green
    migration, a green query and a green test while the frontend sees nothing at all.
    `pushed_at` and `created_at` are on the model and still missing here; `asset_values` is
    not, and `test_a_new_draft_column_reaches_the_api` is what keeps it that way.
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
    zernio_post_id: str | None
    lineage: dict


def _lineage(session: SessionDep, draft: Draft) -> dict:
    """Names for the exact versions this draft was generated from."""

    def named(family: str | None, version: int | None) -> dict | None:
        if not family:
            return None
        template = session.exec(
            select(Template)
            .where(Template.family_id == family, Template.version == version)
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
        visual_png=(
            base64.b64encode(draft.visual_image).decode() if draft.visual_image else None
        ),
        zernio_post_id=draft.zernio_post_id,
        lineage=_lineage(session, draft),
    )


def _load(session: SessionDep, draft_id: int) -> Draft:
    draft = session.get(Draft, draft_id)
    if draft is None:
        raise HTTPException(status_code=404, detail=f"no draft {draft_id}")
    return draft


def _renderer(session: SessionDep, draft: Draft, html_renderer, image_renderer):
    """Whichever renderer the draft's visual template declares."""
    template = session.exec(
        select(Template).where(Template.family_id == draft.visual_family)
        .order_by(desc(col(Template.version)))
    ).first()
    declared = template.body.get("renderer") if template else "html"
    return image_renderer if declared == "ai" else html_renderer


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
    """Turn an idea into a reviewable draft. Nothing leaves the building here."""
    visual = session.get(Template, payload.visual_id) if payload.visual_id else None
    renderer = (
        image_renderer if visual and visual.body.get("renderer") == "ai" else html_renderer
    )
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


@router.post("/{draft_id}/regenerate-text")
def rewrite(session: SessionDep, llm: LLMDep, draft_id: int) -> DraftOut:
    """Rewrite the words against the same templates. Lineage does not move."""
    draft = _load(session, draft_id)
    try:
        regenerate_text(session, llm, draft)
    except (NoUsableTemplates, LLMResponseError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
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
    """Redraw the image from the values already written. The words are untouched."""
    draft = _load(session, draft_id)
    regenerate_visual(session, draft, _renderer(session, draft, html_renderer, image_renderer))
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
    try:
        push_draft(session, draft, zernio)
    except PushFailed as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    session.commit()
    session.refresh(draft)
    return _out(session, draft)


@router.post("/autonomous-run")
def autonomous_run(
    session: SessionDep,
    llm: LLMDep,
    html_renderer: HtmlRendererDep,
    cap: int | None = None,
) -> dict:
    """Run unattended generation now, capped. Produces drafts here; pushes nothing."""
    try:
        result = run_autonomous(
            session,
            llm,
            html_renderer,
            cap=cap if cap is not None else settings.autonomous_max_drafts,
            notify=notify,
        )
    except AutonomousRunFailed as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    session.commit()
    return {
        "created": result.created,
        "failed": result.failed,
        # A draft that was created and has no picture. Reported beside `created` rather than
        # left to whoever opens the queue: this endpoint's answer is the only thing a caller
        # sees, and "3 created" with the images missing is the failure US-009 came to close.
        "visuals_failed": result.visuals_failed,
        "topics": result.topics,
    }
