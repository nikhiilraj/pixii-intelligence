# 05 — Coverage count visible in TemplateManager

**What to build:** Every template in Studio shows how many corpus posts it covers, so the
number that now decides approval is legible to the person reading the list.

`provenance` is already on the object the templates API returns, so this is frontend-only —
no route change, no schema change.

This lands early on purpose: it is the instrument the one-time validation read uses. After
ticket 02 runs for the first time, someone opens this list, reads the coverage counts, and
picks three posts a high-coverage pattern claims in order to check whether the pattern
actually describes them. Without the count on screen that check means writing SQL.

Two rules from CLAUDE.md apply directly and are not negotiable here:

- **Never rank.** A count is not a ranking. Do not add a sort-by-coverage control, do not
  order the list by it, do not label anything best or top.
- **Print `—`, never `0`, where data was never collected.** A hand-authored template has no
  provenance because none was ever recorded, which is not the same as covering zero posts.
  The 15 structures currently carrying empty provenance are exactly this case.

Anything visual gets looked at in a browser at 390px and 1440px, in light and dark — jsdom
does not count as having seen it.

**Blocked by:** None — can start immediately, in parallel with the whole chain.

**Status:** ready-for-agent

- [ ] Each template row shows its coverage count
- [ ] A template with no recorded provenance shows `—`, not `0`
- [ ] No sort, filter or label that ranks templates by coverage
- [ ] Checked in a real browser at 390px and 1440px, light and dark
