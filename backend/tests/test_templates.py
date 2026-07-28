import pytest

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


def a_hook(session, **kwargs) -> Template:
    defaults = dict(
        kind=TemplateKind.HOOK,
        name="transformation",
        body={"pattern": "{small_value} turned into {large_value} {unit}"},
        slots=[{"name": "small_value", "example": "$450"}],
        provenance=["6a26f1402b2567671a2daa62"],
    )
    defaults.update(kwargs)
    return create_template(session, **defaults)


def test_a_new_template_starts_at_version_one_awaiting_approval(session):
    template = a_hook(session)

    assert template.version == 1
    assert template.status == TemplateStatus.PROPOSED
    assert template.family_id


def test_persists_kind_slots_provenance_status_and_version(session):
    template = a_hook(session)

    assert template.kind == TemplateKind.HOOK
    assert template.body["pattern"].startswith("{small_value} turned into")
    assert template.slots == [{"name": "small_value", "example": "$450"}]
    assert template.provenance == ["6a26f1402b2567671a2daa62"]


def test_editing_creates_a_new_version_and_leaves_the_prior_one_intact(session):
    original = a_hook(session)
    approve(session, original)

    revised = edit_template(session, original, body={"pattern": "{a} became {b}"})

    assert revised.version == 2
    assert revised.family_id == original.family_id
    assert revised.id != original.id
    assert original.body["pattern"].startswith("{small_value} turned into")
    assert original.version == 1


def test_an_edited_version_carries_the_status_it_was_edited_from(session):
    """The strategist editing in the UI is the approver — re-approval would be friction."""
    original = a_hook(session)
    approve(session, original)

    revised = edit_template(session, original, name="renamed")

    assert revised.status == TemplateStatus.APPROVED


def test_a_retired_template_cannot_be_edited(session):
    template = a_hook(session)
    approve(session, template)
    retire(session, template)

    with pytest.raises(RetiredTemplateError):
        edit_template(session, template, name="resurrect")


def test_retiring_preserves_the_template_and_its_history(session):
    original = a_hook(session)
    approve(session, original)
    revised = edit_template(session, original, name="v2")

    retire(session, revised)

    assert revised.status == TemplateStatus.RETIRED
    assert session.get(Template, original.id) is not None
    assert session.get(Template, revised.id) is not None


def test_only_approved_templates_are_usable_for_generation(session):
    proposed = a_hook(session, name="proposed-one")
    approved = a_hook(session, name="approved-one")
    approve(session, approved)
    retired = a_hook(session, name="retired-one")
    approve(session, retired)
    retire(session, retired)

    usable = usable_templates(session, TemplateKind.HOOK)

    assert [t.name for t in usable] == ["approved-one"]
    assert proposed.status == TemplateStatus.PROPOSED


def test_only_the_newest_version_of_a_family_is_usable(session):
    original = a_hook(session)
    approve(session, original)
    revised = edit_template(session, original, name="newer")

    usable = usable_templates(session, TemplateKind.HOOK)

    assert [t.id for t in usable] == [revised.id]


def test_usable_templates_are_scoped_to_their_kind(session):
    hook = a_hook(session)
    approve(session, hook)
    structure = create_template(
        session,
        kind=TemplateKind.STRUCTURE,
        name="offer-reward",
        body={"sections": []},
    )
    approve(session, structure)

    assert [t.name for t in usable_templates(session, TemplateKind.STRUCTURE)] == ["offer-reward"]


def test_latest_versions_shows_one_row_per_family_whatever_its_status(session):
    original = a_hook(session)
    edit_template(session, original, name="v2")
    a_hook(session, name="another-family")

    latest = latest_versions(session)

    assert sorted(t.name for t in latest) == ["another-family", "v2"]


def test_a_visual_template_declares_its_renderer(session):
    template = create_template(
        session,
        kind=TemplateKind.VISUAL,
        name="stat-hero",
        body={"renderer": "html", "component": "stat_hero"},
    )

    assert template.body["renderer"] == "html"
