# 06 — The leftover loop: see what nothing covers, and extract from just that

**What to build:** A number that tells you how much of the corpus no template speaks for,
and a way to point extraction at exactly those posts — so "is the model leaving something
out?" becomes a figure that goes down instead of a worry.

After the first real run, **144 of 236 posts are cited by no template.** Nobody knows
whether that is because those posts share no shape, or because a model handed 274 posts at
once stopped labelling early. The loop settles it: run over the leftovers, see what comes
back, repeat until two rounds return nothing new.

Three parts:

- **The set.** Posts of a cohort and platform that no live template cites, per kind. "Live"
  means the newest version of each non-RETIRED family — reuse `templates.latest_versions`.
  Any row would be wrong: a v1 that cited a post and a v2 that dropped it means the current
  library does not cover that post, and counting the v1 would hide exactly the gap this
  exists to show.
- **The number, on screen.** Beside the extract buttons, where someone would act on it.
  Per kind, since the extract routes are per kind.
- **The run.** The existing extract routes take a flag that narrows the sample to the
  uncovered set. Everything downstream is unchanged — same prompt, same coverage gate, same
  reconciliation.

An empty uncovered set must return no proposals rather than raising: that is the loop
terminating normally, and it is the state you are trying to reach.

Expect the leftovers to yield less than the first pass did, and expect that to be correct
rather than a failure. The prompt refuses a shape that appears once, so a run over three
stragglers should return nothing at all.

**Blocked by:** None — 01 through 05 have landed and are committed.

**Status:** done — not committed, left in the working tree

- [x] The uncovered set counts the newest version of each non-retired family, not every row
- [x] The count appears beside the extract controls, per kind
- [x] Extraction can be pointed at only the uncovered posts, with everything downstream unchanged
- [x] An empty uncovered set returns no proposals and does not raise
- [x] The count falls after a run that covers new posts — proved by a test, not by reasoning
