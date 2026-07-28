import httpx
from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel
from sqlmodel import col, select

from app.deps import HtmlRendererDep, LLMDep, SessionDep
from app.extraction import (
    DEFAULT_SAMPLE_SIZE,
    ExtractionError,
    compatible_hooks,
    propose_hooks,
    propose_structures,
)
from app.llm import LLMResponseError
from app.models.template import Template, TemplateKind, TemplateStatus
from app.rendering import MissingSlotValue, UnsupportedRenderer, render_visual
from app.templates import (
    RetiredTemplateError,
    approve,
    create_template,
    edit_template,
    latest_versions,
    retire,
    usable_templates,
)

router = APIRouter(prefix="/templates", tags=["templates"])


class TemplateIn(BaseModel):
    kind: TemplateKind
    name: str
    body: dict = {}
    slots: list[dict] = []
    provenance: list[str] = []
    notes: str = ""


class TemplateEdit(BaseModel):
    name: str | None = None
    body: dict | None = None
    slots: list[dict] | None = None
    provenance: list[str] | None = None
    notes: str | None = None


def _load(session: SessionDep, template_id: int) -> Template:
    template = session.get(Template, template_id)
    if template is None:
        raise HTTPException(status_code=404, detail=f"no template {template_id}")
    return template


@router.get("")
def list_templates(
    session: SessionDep,
    kind: TemplateKind | None = None,
    usable_only: bool = False,
) -> list[Template]:
    """The current version of each family. `usable_only` narrows to what generation may use."""
    if usable_only:
        if kind is None:
            raise HTTPException(status_code=400, detail="usable_only requires a kind")
        return usable_templates(session, kind)
    return latest_versions(session, kind)


@router.get("/{template_id}/versions")
def list_versions(session: SessionDep, template_id: int) -> list[Template]:
    """Every version of this template's family, oldest first — the attribution trail."""
    template = _load(session, template_id)
    statement = (
        select(Template)
        .where(Template.family_id == template.family_id)
        .order_by(col(Template.version))
    )
    return list(session.exec(statement).all())


@router.post("/extract/hooks", status_code=201)
def extract_hooks(session: SessionDep, llm: LLMDep, platform: str = "linkedin") -> list[Template]:
    """Ask the model for hook patterns from the strongest posts.

    Everything it returns arrives as a proposal — a human still approves before any of it
    can be used for generation.
    """
    try:
        proposals = propose_hooks(session, llm, platform=platform)
    except (ExtractionError, LLMResponseError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    session.commit()
    for proposal in proposals:
        session.refresh(proposal)
    return proposals


@router.post("/extract/structures", status_code=201)
def extract_structures(
    session: SessionDep,
    llm: LLMDep,
    platform: str = "linkedin",
    sample_size: int = DEFAULT_SAMPLE_SIZE,
    focus: str = "",
) -> list[Template]:
    """Ask the model for post structures from the strongest posts. Proposals only.

    `sample_size` widens the evidence base. Post types that consistently underperform
    (research posts, in this corpus) sit below the default cutoff, so deriving a structure
    for one requires deliberately looking further down the ranking.
    """
    try:
        proposals = propose_structures(
            session, llm, platform=platform, sample_size=sample_size, focus=focus
        )
    except (ExtractionError, LLMResponseError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    session.commit()
    for proposal in proposals:
        session.refresh(proposal)
    return proposals


@router.post("/{template_id}/preview")
def preview_visual(
    session: SessionDep,
    renderer: HtmlRendererDep,
    template_id: int,
    values: dict[str, str],
) -> Response:
    """Render a visual template with real values and return the image."""
    template = _load(session, template_id)
    if template.kind is not TemplateKind.VISUAL:
        raise HTTPException(status_code=400, detail="only visual templates render")
    try:
        image = render_visual(template, values, renderer)
    except MissingSlotValue as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except UnsupportedRenderer as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    except httpx.HTTPStatusError as exc:
        # The rendering service failing is not this service failing. Report which it was
        # and what it said, so "rate limited, retry" is distinguishable from "broken".
        raise HTTPException(
            status_code=502,
            detail=f"rendering service returned {exc.response.status_code}",
        ) from exc
    return Response(content=image, media_type="image/png")


@router.get("/{template_id}/compatible-hooks")
def get_compatible_hooks(session: SessionDep, template_id: int) -> list[Template]:
    """The usable hooks a structure declares it pairs with."""
    structure = _load(session, template_id)
    if structure.kind is not TemplateKind.STRUCTURE:
        raise HTTPException(status_code=400, detail="only a structure declares hook pairings")
    return compatible_hooks(session, structure)


@router.post("", status_code=201)
def author_template(session: SessionDep, payload: TemplateIn) -> Template:
    template = create_template(session, **payload.model_dump())
    session.commit()
    session.refresh(template)
    return template


@router.put("/{template_id}")
def revise_template(session: SessionDep, template_id: int, payload: TemplateEdit) -> Template:
    """Write the next version. The edited version stays exactly as it was."""
    template = _load(session, template_id)
    changes = payload.model_dump(exclude_none=True)
    try:
        revised = edit_template(session, template, **changes)
    except RetiredTemplateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    session.commit()
    session.refresh(revised)
    return revised


@router.post("/{template_id}/approve")
def approve_template(session: SessionDep, template_id: int) -> Template:
    template = approve(session, _load(session, template_id))
    session.commit()
    session.refresh(template)
    return template


@router.post("/{template_id}/retire")
def retire_template(session: SessionDep, template_id: int) -> Template:
    template = retire(session, _load(session, template_id))
    session.commit()
    session.refresh(template)
    return template


__all__ = ["router", "TemplateStatus"]
