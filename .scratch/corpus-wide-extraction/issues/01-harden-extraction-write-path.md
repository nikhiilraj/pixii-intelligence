# 01 — Harden the extraction write path

**What to build:** A malformed proposal costs only itself, never its siblings. Today
`propose_hooks` and `propose_structures` end in a bare list comprehension, and
`_to_template` / `_to_structure` raise `ExtractionError` on a blank name — so one bad
proposal out of ten returns a 502 from the extract route and writes nothing at all.
`propose_visuals` already does this correctly (`extraction.py:389-402`); copy that shape.

Also: `_to_structure` passes the model's `source_post_ids` straight through, while
`_to_template` filters to ids the model was actually shown ("provenance has to be
checkable"). Give structures the same filter. Harmless today because structure provenance
is always empty — load-bearing in ticket 04, where provenance becomes the approval gate
and invented ids would auto-approve a template covering nothing.

Nothing about *what* gets extracted changes in this ticket. This is the safety net every
later ticket lands on.

**Blocked by:** None — can start immediately.

**Status:** ready-for-agent

- [ ] A batch containing one unusable proposal writes the usable ones and returns 201, not 502
- [ ] The rejection is logged with the exception type, as `propose_visuals` does
- [ ] Structure provenance keeps only post ids present in the sample the model was shown
- [ ] Coverage of both: a test that fails if the per-proposal catch is removed
