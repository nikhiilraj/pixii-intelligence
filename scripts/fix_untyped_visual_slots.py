#!/usr/bin/env python3
"""Take the untyped visual templates out of the way of the empty-box failure.

Run with the backend's own interpreter, from `backend/`, so `app` and the `.env` beside
it resolve:

    cd backend && .venv/bin/python ../scripts/fix_untyped_visual_slots.py

Two corrections, both consequences of `WRITABLE_SLOT_TYPES` no longer treating an untyped
slot as writable:

1. **`stat-hero` v1 is retired.** It is APPROVED with four untyped slots, two of them an
   `<img src>` — the shape that produced a brand-correct image with empty boxes and
   `visual_error: None`. v2 has the same four slots typed `text,text,image_url,image_url`,
   and `usable_templates` only ever offered v2 (newest version per family, then APPROVED),
   so retiring v1 changes nothing generation could reach on its own. It closes the door on
   a caller naming the version explicitly, and it stops the row reading as usable library.
   Retired rather than deleted: draft 21 was generated from it and attribution must survive.

2. **The two PROPOSED `ai` visuals get their `subject` slot typed `text`.** Untyped is no
   longer writable, so approving either of them as they stand would leave the model with no
   slot to fill and the render failing on a leftover placeholder. Their `subject` is prose
   for an image prompt, which is exactly what `text` means.

Updated **in place**, not through `edit_template`. Versioning exists to keep a draft's
lineage pointing at the version it was generated from, and no draft references either family
— they have never been approved, so generation has never been able to select one.

Idempotent: each correction is guarded on the state it changes, so a second run reports
`retired=0 typed=0`.
"""

from __future__ import annotations

from app.db import engine
from app.models.template import Template, TemplateStatus
from app.templates import retire
from sqlmodel import Session

# Ids in the live library, each pinned to the name and version it is expected to be so a
# mismatched database is a loud stop rather than the wrong row edited.
UNTYPED_APPROVED = (556, "stat-hero", 1)
UNTYPED_PROPOSALS = ((557, "lifestyle-scene", 1), (798, "product-on-cream", 1))

# The one type these proposals' slots can have: a subject description written into an image
# prompt is prose.
SUBJECT_TYPE = "text"


def _load(session: Session, template_id: int, name: str, version: int) -> Template:
    template = session.get(Template, template_id)
    if template is None or (template.name, template.version) != (name, version):
        raise SystemExit(f"template {template_id} is not {name} v{version} — check the database")
    return template


def main() -> int:
    retired = typed = 0
    with Session(engine) as session:
        stat_hero_v1 = _load(session, *UNTYPED_APPROVED)
        if stat_hero_v1.status is not TemplateStatus.RETIRED:
            retire(session, stat_hero_v1)
            retired += 1

        for template_id, name, version in UNTYPED_PROPOSALS:
            template = _load(session, template_id, name, version)
            if any(slot.get("type") for slot in template.slots):
                continue
            # Reassigned rather than mutated: SQLAlchemy does not track an in-place edit of
            # a JSONB list, so mutating it would commit nothing.
            template.slots = [{**slot, "type": SUBJECT_TYPE} for slot in template.slots]
            session.add(template)
            typed += 1

        session.commit()

    print(f"retired={retired} typed={typed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
