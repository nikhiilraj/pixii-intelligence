import uuid

from sqlmodel import Session, col, func, select

from app.models.template import Template, TemplateKind, TemplateStatus


class RetiredTemplateError(RuntimeError):
    """A retired template is history. Revive it by authoring a new one instead."""


def create_template(
    session: Session,
    *,
    kind: TemplateKind,
    name: str,
    body: dict | None = None,
    slots: list[dict] | None = None,
    provenance: list[str] | None = None,
    notes: str = "",
    status: TemplateStatus = TemplateStatus.PROPOSED,
) -> Template:
    """Author version 1 of a new template family.

    Defaults to proposed, and every hand-authored caller takes that default: nothing enters
    the usable library unvetted by accident.

    **Hook and structure extraction now pass APPROVED explicitly when a pattern's filtered
    provenance covers `extraction.APPROVE_AT_COVERAGE` posts or more**, so "a human approves
    everything" is no longer true of this function and the old docstring saying so was the
    kind of comment this codebase treats as a bug. What that gate protected — nothing is lost — is
    bought by the append-only write, not by the click; the 48 unreviewed proposals sitting in
    Studio are what a review gate with a backlog actually protects. See design decision 3 in
    `docs/superpowers/specs/2026-08-06-corpus-wide-extraction-design.md`.
    """
    template = Template(
        family_id=uuid.uuid4().hex,
        version=1,
        kind=kind,
        name=name,
        body=body or {},
        slots=slots or [],
        provenance=provenance or [],
        notes=notes,
        status=status,
    )
    session.add(template)
    session.flush()
    return template


def edit_template(session: Session, template: Template, **changes) -> Template:
    """Write the next version of a family, leaving the edited version untouched.

    The new version carries the status it was edited from: a strategist editing an
    approved template in the UI is already the approver, so demanding re-approval would
    be friction without adding review.
    """
    if template.status is TemplateStatus.RETIRED:
        raise RetiredTemplateError(
            f"template {template.name} (family {template.family_id}) is retired"
        )

    highest = session.exec(
        select(func.max(col(Template.version))).where(Template.family_id == template.family_id)
    ).one()

    revised = Template(
        family_id=template.family_id,
        version=(highest or template.version) + 1,
        kind=template.kind,
        name=changes.get("name", template.name),
        body=changes.get("body", template.body),
        slots=changes.get("slots", template.slots),
        provenance=changes.get("provenance", template.provenance),
        notes=changes.get("notes", template.notes),
        status=template.status,
    )
    session.add(revised)
    session.flush()
    return revised


def approve(session: Session, template: Template) -> Template:
    return _set_status(session, template, TemplateStatus.APPROVED)


def retire(session: Session, template: Template) -> Template:
    """Take a template out of use without deleting it — attribution must survive."""
    return _set_status(session, template, TemplateStatus.RETIRED)


def _set_status(session: Session, template: Template, status: TemplateStatus) -> Template:
    template.status = status
    session.add(template)
    session.flush()
    return template


def _latest_version_ids():
    """Ids of the newest version of every family."""
    newest = (
        select(
            col(Template.family_id).label("family_id"),
            func.max(col(Template.version)).label("version"),
        )
        .group_by(col(Template.family_id))
        .subquery()
    )
    return select(col(Template.id)).join(
        newest,
        (col(Template.family_id) == newest.c.family_id)
        & (col(Template.version) == newest.c.version),
    )


def latest_versions(session: Session, kind: TemplateKind | None = None) -> list[Template]:
    """The current version of every family, whatever its status — what the editor lists."""
    statement = select(Template).where(col(Template.id).in_(_latest_version_ids()))
    if kind is not None:
        statement = statement.where(Template.kind == kind)
    return list(session.exec(statement.order_by(col(Template.name))).all())


def usable_templates(session: Session, kind: TemplateKind) -> list[Template]:
    """Templates generation may actually use: newest version, approved, of this kind.

    Proposed templates have not been reviewed and retired ones were withdrawn, so
    neither is offered to the generator.
    """
    return [t for t in latest_versions(session, kind) if t.status is TemplateStatus.APPROVED]
