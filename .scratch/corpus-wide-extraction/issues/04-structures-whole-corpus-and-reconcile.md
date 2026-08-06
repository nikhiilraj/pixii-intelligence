# 04 — Structures read the whole corpus and reconcile

**What to build:** Everything tickets 02 and 03 did for hooks, now for structures — and
structure provenance stops being empty for the first time.

All 15 structures in the database today cite zero posts. `_to_structure` reads
`source_post_ids`, but the structures prompt's example JSON never asks for it and the
schema does not mark it required, so the model never sends it. Requiring it is the fix —
and it only becomes safe because ticket 01 added the same provenance filter hooks have.
Provenance is the approval gate now, so an unfiltered list of invented ids would
auto-approve a template covering nothing.

Structures send full post text rather than 220-char openings (~43k tokens over ~274
posts), since a structure is about the shape of the whole thing.

Also delete `sample_size` from `extract_structures`. Its docstring sells it as the way to
reach post types below the engagement cutoff; whole-corpus extraction makes it meaningless,
and a parameter that silently does nothing is exactly what this codebase's comments exist
to prevent. `focus` stays — naming a post type to describe is still a real request.

Last in the chain because structures reuse the sample, status and reconcile machinery from
02 and 03, and take the hook list as input for `compatible_hooks`.

**Blocked by:** 03.

**Status:** ready-for-agent

- [ ] Structure extraction reads every non-excluded post of the cohort, full text
- [ ] Every returned structure carries a non-empty filtered provenance
- [ ] Coverage gates status and reconcile promotes, identically to hooks
- [ ] `sample_size` is gone from the route, and `focus` still works
- [ ] `compatible_hooks` still resolves against usable hooks
