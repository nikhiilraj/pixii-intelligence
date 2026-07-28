from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlmodel import col, select

from app.deps import LLMDep, SessionDep
from app.extraction import ExtractionError, propose_hooks
from app.llm import LLMResponseError
from app.models.template import Template, TemplateKind, TemplateStatus
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
