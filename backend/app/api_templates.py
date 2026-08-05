import httpx
from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel
from sqlmodel import col, select

from app.assets import UnresolvableAsset
from app.deps import HtmlRendererDep, ImageRendererDep, LLMDep, SessionDep
from app.extraction import (
    DEFAULT_SAMPLE_SIZE,
    Cohort,
    ExtractionError,
    compatible_hooks,
    propose_hooks,
    propose_structures,
    propose_visuals,
)
from app.generation import render_template
from app.llm import LLMResponseError
from app.models.template import Template, TemplateKind, TemplateStatus
from app.rendering import ImageGenerationError, MissingSlotValue, UnsupportedRenderer
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


class PNGResponse(Response):
    """A rendered template, including an accurate OpenAPI media type."""

    media_type = "image/png"


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
    status: TemplateStatus | None = None,
    usable_only: bool = False,
) -> list[Template]:
    """The current version of each family. `usable_only` narrows to what generation may use.

    `status` is what makes the review queue answerable at all. `latest_versions` is
    status-blind and `usable_only` is APPROVED-only, so before this parameter there was no
    query anywhere for "proposed and not yet reviewed" — the Inbox's first gate, and the one
    with 37 things sitting behind it.

    Typed as the enum, so an unknown status is rejected with a 422 naming the three allowed
    values rather than silently matching nothing and reading as an empty queue.
    """
    if usable_only:
        if kind is None:
            raise HTTPException(status_code=400, detail="usable_only requires a kind")
        if status is not None:
            # Refused rather than resolved either way. `usable_only` already means
            # `status=approved`, so honouring both would silently ignore one of them — and an
            # ignored `status=proposed` returns approved templates while claiming to be a
            # review queue.
            raise HTTPException(
                status_code=422,
                detail=(
                    f"usable_only already means status={TemplateStatus.APPROVED.value}; "
                    "pass one or the other, not both"
                ),
            )
        return usable_templates(session, kind)

    templates = latest_versions(session, kind)
    if status is not None:
        # Filtered after `latest_versions`, in Python, exactly as `usable_templates` filters.
        # The order is load-bearing: pushing status into the newest-version subquery would
        # return the newest *proposed* version of a family whose current version is approved,
        # i.e. a superseded proposal presented as awaiting review.
        return [t for t in templates if t.status is status]
    return templates


@router.get("/{template_id}/versions")
def list_versions(session: SessionDep, template_id: int) -> list[Template]:
    """Every version of this template's family, oldest first — the attribution trail.

    **No screen reads this, and that is recorded rather than fixed.** Lineage resolves through
    `(family_id, version)` everywhere in this system, and this is the one route that shows a
    family's history; the natural consumer is a version-history disclosure on the `/templates`
    review row. It was left unwired in the operator-controls pass because it is a *display* of
    lineage beside controls that already work, where four operations had no entry point at
    all. See `docs/capability-matrix.md`. Anyone connecting it should put it there, not on a
    screen of its own.
    """
    template = _load(session, template_id)
    statement = (
        select(Template)
        .where(Template.family_id == template.family_id)
        .order_by(col(Template.version))
    )
    return list(session.exec(statement).all())


@router.post("/extract/hooks", status_code=201)
def extract_hooks(
    session: SessionDep,
    llm: LLMDep,
    platform: str = "linkedin",
    cohort: Cohort = Cohort.VOICE,
) -> list[Template]:
    """Ask the model for hook patterns from the strongest posts.

    Everything it returns arrives as a proposal — a human still approves before any of it
    can be used for generation. `cohort=inspiration` reads other creators' posts instead
    of Monte's; the resulting hooks are still only shapes, never a voice.
    """
    try:
        proposals = propose_hooks(session, llm, platform=platform, cohort=cohort)
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
    cohort: Cohort = Cohort.VOICE,
) -> list[Template]:
    """Ask the model for post structures from the strongest posts. Proposals only.

    `sample_size` widens the evidence base. Post types that consistently underperform
    (research posts, in this corpus) sit below the default cutoff, so deriving a structure
    for one requires deliberately looking further down the ranking. `cohort=inspiration`
    reads other creators' posts instead of Monte's.
    """
    try:
        proposals = propose_structures(
            session,
            llm,
            platform=platform,
            sample_size=sample_size,
            focus=focus,
            cohort=cohort,
        )
    except (ExtractionError, LLMResponseError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    session.commit()
    for proposal in proposals:
        session.refresh(proposal)
    return proposals


@router.post("/extract/visuals", status_code=201)
def extract_visuals(
    session: SessionDep,
    llm: LLMDep,
    html_renderer: HtmlRendererDep,
    platform: str = "linkedin",
    cohort: Cohort = Cohort.VOICE,
) -> list[Template]:
    """Ask the model for visual layouts from the images of the strongest posts.

    Slower than the other two extract routes by design: each proposal is rendered once
    before it is offered, so nothing reaches the review queue that cannot be drawn.
    """
    try:
        proposals = propose_visuals(session, llm, html_renderer, platform=platform, cohort=cohort)
    except (ExtractionError, LLMResponseError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    session.commit()
    for proposal in proposals:
        session.refresh(proposal)
    return proposals


class PreviewIn(BaseModel):
    body: dict = {}
    slots: list[dict] = []
    values: dict[str, str] = {}


@router.post("/preview", response_class=PNGResponse)
def preview_unsaved(
    session: SessionDep,
    html_renderer: HtmlRendererDep,
    image_renderer: ImageRendererDep,
    payload: PreviewIn,
) -> PNGResponse:
    """Render a visual body that has not been saved, for the template editor.

    An unsaved edit has no id, so this takes the body and slots directly. The row is
    built in memory and never added to the session — previewing must not be able to
    write a template, and a `Template(...)` that is never `session.add`ed cannot.

    **`html` only.** The saved route guards on `kind`, which this route cannot: it
    constructs the row, so `kind` is whatever it says. Without this check a body reading
    `{"renderer": "ai", "prompt": "..."}` reaches `AzureImageRenderer` — a paid image
    generation triggered from a textarea, with no id, no rate limit and nothing saved to
    show for it. The editor only ever previews markup, so refusing the other renderer
    costs nothing.
    """
    if payload.body.get("renderer") != "html":
        raise HTTPException(
            status_code=501,
            detail="unsaved preview renders html only; save the template to preview an ai visual",
        )
    draft = Template(
        family_id="preview", version=0, kind=TemplateKind.VISUAL,
        name="preview", body=payload.body, slots=payload.slots,
    )
    return _rendered(session, draft, payload.values, html_renderer, image_renderer)


@router.post("/{template_id}/preview", response_class=PNGResponse)
def preview_visual(
    session: SessionDep,
    html_renderer: HtmlRendererDep,
    image_renderer: ImageRendererDep,
    template_id: int,
    values: dict[str, str],
) -> PNGResponse:
    """Render a visual template with real values and return the image.

    The template's declared renderer selects which of the two paths runs. `values` arrives
    straight from the request body, so an `image_url` slot here holds whatever the caller
    put there — it goes through the same `resolve_asset_values` generation uses, or a
    picked asset would preview as an empty box while generating fine.
    """
    template = _load(session, template_id)
    if template.kind is not TemplateKind.VISUAL:
        raise HTTPException(status_code=400, detail="only visual templates render")
    return _rendered(session, template, values, html_renderer, image_renderer)


def _rendered(
    session: SessionDep, template: Template, values: dict[str, str],
    html_renderer, image_renderer,
) -> PNGResponse:
    renderer = image_renderer if template.body.get("renderer") == "ai" else html_renderer
    try:
        image = render_template(session, template, values, renderer)
    except (MissingSlotValue, UnresolvableAsset) as exc:
        # Both are the caller handing us values we cannot render: a slot with nothing in
        # it, or a slot holding something that is not an asset.
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except UnsupportedRenderer as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    except ImageGenerationError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except httpx.HTTPStatusError as exc:
        # The rendering service failing is not this service failing. Report which it was
        # and what it said, so "rate limited, retry" is distinguishable from "broken".
        raise HTTPException(
            status_code=502,
            detail=f"rendering service returned {exc.response.status_code}",
        ) from exc
    return PNGResponse(content=image)


@router.get("/{template_id}/compatible-hooks")
def get_compatible_hooks(session: SessionDep, template_id: int) -> list[Template]:
    """The usable hooks a structure declares it pairs with.

    Structure extraction asks the model for `compatible_hooks` and stores the resolved families
    on the row; this route turns them back into approved hooks. Its reader is Studio's template
    picker, which groups the hook list under the structure that was chosen — see
    `frontend/src/app/studio/Studio.tsx`.

    **What it answers with is a grouping and never a shortlist**, which is why the picker groups
    rather than filters: `compatible_hook_families` names families, so a pairing survives its
    hooks being retired and resolves here to fewer rows than were recorded — against the live
    library each of the two approved structures resolves to exactly one of six approved hooks.
    An empty list is therefore a routine answer, and a caller treating it as "these are the only
    hooks that may be used" would leave an operator with one option out of six.

    Both of the states above are HTTP-tested in `test_api_templates.py`, along with the 400
    below: `test_structures.py` covers `extraction.compatible_hooks` at the function and this
    handler was, until then, exercised by nothing — the class of gap the README's mutation audit
    found twice before.
    """
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
