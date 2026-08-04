from fastapi.testclient import TestClient

from app.db import get_session
from app.deps import get_html_renderer
from app.main import app
from app.models.draft import Draft
from app.models.template import TemplateKind
from app.templates import approve, create_template


def a_draft_with_visual(session, *, image: bytes, previous: bytes | None = None) -> Draft:
    """A draft with a rendered visual, copying the construction in test_publishing.a_draft."""
    hook = create_template(session, kind=TemplateKind.HOOK, name="transformation", body={})
    structure = create_template(session, kind=TemplateKind.STRUCTURE, name="case-loop", body={})
    visual = create_template(
        session,
        kind=TemplateKind.VISUAL,
        name="stat-card",
        body={"renderer": "html", "html": "<p>a card</p>"},
    )
    for template in (hook, structure, visual):
        approve(session, template)

    draft = Draft(
        idea="an idea",
        mode="directed",
        hook_family=hook.family_id,
        hook_version=hook.version,
        structure_family=structure.family_id,
        structure_version=structure.version,
        visual_family=visual.family_id,
        visual_version=visual.version,
        hook_text="A hook line.",
        body_text="The body.",
        visual_image=image,
        previous_visual=previous,
    )
    session.add(draft)
    session.flush()
    return draft


class ConstantRenderer:
    """Renders the same bytes every time, regardless of markup."""

    def __init__(self, image: bytes) -> None:
        self.image = image

    def screenshot(self, html: str, width: int, height: int) -> bytes:
        return self.image


class ExplodingRenderer:
    """Every render fails, the way a dead upstream renderer would."""

    def screenshot(self, html: str, width: int, height: int) -> bytes:
        raise RuntimeError("renderer unavailable")


def test_a_redraw_keeps_the_image_it_replaced(session):
    """Two images answer 'is this better than what I had', which is the question asked."""
    draft = a_draft_with_visual(session, image=b"FIRST")
    session.commit()

    # `TestClient(app)` otherwise gets its own `get_session()` — a fresh connection that
    # cannot see this test's row without a real commit crossing connections. See
    # test_template_preview.py for the same fix on the same omission.
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_html_renderer] = lambda: ConstantRenderer(b"SECOND")
    try:
        response = TestClient(app).post(f"/drafts/{draft.id}/regenerate-visual")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["has_previous_visual"] is True
    session.refresh(draft)
    assert draft.visual_image == b"SECOND"
    assert draft.previous_visual == b"FIRST"


def test_the_previous_image_is_downloadable(session):
    draft = a_draft_with_visual(session, image=b"FIRST", previous=b"OLDER")
    session.commit()

    app.dependency_overrides[get_session] = lambda: session
    try:
        response = TestClient(app).get(f"/drafts/{draft.id}/previous-visual")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.content == b"OLDER"


def test_restoring_swaps_them_rather_than_discarding(session):
    """Restore is itself undoable — a swap, not a rollback, so neither image is lost."""
    draft = a_draft_with_visual(session, image=b"NEW", previous=b"OLD")
    session.commit()

    app.dependency_overrides[get_session] = lambda: session
    try:
        response = TestClient(app).post(f"/drafts/{draft.id}/restore-visual")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    session.refresh(draft)
    assert draft.visual_image == b"OLD"
    assert draft.previous_visual == b"NEW"


def test_restoring_with_nothing_to_restore_is_a_409(session):
    draft = a_draft_with_visual(session, image=b"ONLY")
    session.commit()

    app.dependency_overrides[get_session] = lambda: session
    try:
        response = TestClient(app).post(f"/drafts/{draft.id}/restore-visual")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 409


def test_a_failed_redraw_does_not_destroy_the_image_that_worked(session):
    """The previous image is only kept when there is a new one to replace it with.

    `_draw_visual` records a failure by setting `visual_image = None`. Moving that None
    into `previous_visual` would turn one bad render into the loss of both pictures.
    """
    draft = a_draft_with_visual(session, image=b"GOOD", previous=b"OLDER")
    session.commit()

    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_html_renderer] = lambda: ExplodingRenderer()
    try:
        TestClient(app).post(f"/drafts/{draft.id}/regenerate-visual")
    finally:
        app.dependency_overrides.clear()

    session.refresh(draft)
    assert draft.previous_visual == b"OLDER"
