# 02 — Hooks read the whole corpus, coverage decides status

**What to build:** Pressing *Extract hooks* reads every non-excluded post in the cohort
instead of the 12 strongest, and returns at most 10 patterns that actually **recur** —
each citing the posts it covers. A pattern covering 5 or more posts arrives APPROVED; the
rest arrive PROPOSED for an optional human look.

Three parts:

- **Sample.** `_strongest_posts` gains a whole-corpus mode: no `limit`, no engagement
  ordering. Every existing exclusion stays — cohort separation, `excluded_from_extraction`,
  `voice_since` on VOICE, non-empty content. ~274 posts, ~14k tokens at 220 chars each.
- **Prompt.** New major version of `extraction.hooks`. The instruction inverts from
  "abstract these posts" to "find the patterns that recur, return at most 10, and list
  every post id each one covers". `source_post_ids` becomes **required** in the schema.
- **Status.** At write time, from the filtered provenance length: `>= 5` → APPROVED,
  below → PROPOSED.

The 12-post limit is what causes the defect: with nothing repeating in the sample, the
model transcribes each post. Hook provenance currently averages 1.09 posts per template.

**Blocked by:** 01 — the per-proposal catch must exist before a prompt this different
starts producing proposals.

**Status:** ready-for-agent

- [ ] Extraction reads every non-excluded post of the cohort; no engagement ordering remains
- [ ] Every exclusion that applied before still applies
- [ ] At most 10 proposals come back, each with a non-empty filtered provenance
- [ ] Coverage >= 5 writes APPROVED; coverage < 5 writes PROPOSED
- [ ] The threshold is one named constant, not a literal at the comparison site
