# 03 — Extraction reconciles into existing families instead of minting

**What to build:** Run *Extract hooks* twice and the family count stops growing. Today
every proposal calls `create_template`, which mints `family_id=uuid.uuid4().hex`
unconditionally — nothing in the extraction path ever looks up an existing template. That
is the whole explanation for 42 hook families drawn from roughly the same 12 posts.

- **Prompt.** The families that already have names go into the request, with their ids:
  "if one of your patterns is the same shape as an existing one, return its `family_id`
  rather than inventing a new name for it." `_structure_prompt` already uses this exact
  mechanism to let a structure cite a hook by name — point it at the template's own kind.
- **Write.** A cited `family_id` that exists → `edit_template`, writing v+1. Not cited →
  `create_template`, a genuinely new family.
- **Promotion.** `edit_template` copies the status of the row it revises
  (`templates.py:69`). So a PROPOSED family that has since crossed the coverage floor must
  be **explicitly promoted** to APPROVED — otherwise a family that lands at coverage 3
  stays PROPOSED forever, even when a later run finds it covers 40 posts. Coverage growing
  as posts arrive is the entire point. APPROVED is never touched whatever coverage does;
  nothing is ever auto-demoted or auto-retired.

A proposal citing a RETIRED family needs no branch: `edit_template` already raises
`RetiredTemplateError`, and ticket 01's per-proposal catch logs and drops it. A family a
human deliberately retired must never be silently revived — not under its own id, and not
re-minted under a new one.

**Blocked by:** 02.

**Status:** ready-for-agent

- [ ] Two consecutive extractions over an unchanged corpus do not increase the family count
- [ ] A cited existing family gains a version rather than a sibling
- [ ] A PROPOSED family whose coverage has crossed the floor is promoted to APPROVED
- [ ] An APPROVED family whose coverage has fallen below the floor keeps its status
- [ ] A proposal citing a retired family is dropped and logged, and mints no new family
