# Corpus-wide hook and structure extraction — design

**Date:** 2026-08-06
**Scope:** HOOK and STRUCTURE only. VISUAL is deliberately out — see the end.

---

## The problem

Extraction mints a new template family for every proposal, and reads only the 12
strongest posts. Both halves are wrong, and together they produce the state the database
is in now.

```
HOOK       42 families   (7 approved, 35 proposed, 1 retired)
STRUCTURE  15 families   (2 approved, 13 proposed)
posts:  Monte 236 · pixii.creates 13 · Pixii_ai 10 · inspiration 15   (274 with text)
```

Three measurements, not impressions:

1. **`family_id=uuid.uuid4().hex` on every proposal** (`extraction.py:_to_template` →
   `templates.create_template`). Nothing in the extraction path ever looks up an existing
   template. Re-running extract over roughly the same top 12 posts is the whole
   explanation for 42 hook families. `edit_template` — which writes a new version into an
   existing family — already exists and extraction never calls it.

2. **Hook provenance averages 1.09 posts per template.** 43 rows, 47 total citations.
   Nearly every hook is grounded in exactly one post, which makes it a transcription of
   that post rather than a pattern.

3. **All 15 structures have empty provenance.** `_to_structure` reads `source_post_ids`,
   but the structures prompt's example JSON (`prompts/extraction.py:71-81`) never asks for
   it and the schema does not mark it required, so the model never sends it. A structure
   cannot currently be traced to a single post.

And the sample itself cannot answer the question being asked. `_strongest_posts` takes the
top 12 by `engaged_actions`; a shape that recurs across 90 mid-performing posts is
invisible to it. "What is common across the corpus" and "what do the strongest posts do"
are different questions, and only the second is being asked.

The sample was never a context constraint:

| | posts | chars | ≈ tokens |
|---|---|---|---|
| hooks (220 chars each) | 274 | 54,130 | ~14k |
| full post text | 274 | 170,202 | ~43k |

The comment at `extraction.py:22` justifies the limit as keeping "the weak tail" from
diluting the pattern. With 12 posts and no repetition to find, the model has nothing to do
but transcribe each one — the limit causes the defect it was meant to prevent.

---

## Decisions

**1 · Coverage, not engagement, is the filter.** How many corpus posts a pattern actually
covers. This is a property of the corpus and needs no lineage, so it does not collide with
the `Never rank` rule — no template is ever called best, and nothing is sorted by how its
drafts performed. Engagement stops steering extraction entirely.

**2 · Whole corpus, one call per kind.** Every non-excluded post for a cohort, no limit and
no ordering. The model is asked what *recurs*, and must cite the posts each pattern covers.
Coverage falls out of the citations for free.

**3 · Auto-approve on evidence.** `coverage >= 5` → APPROVED, below → PROPOSED. This is the
answer to "we don't want a human at this gate": the 48 unreviewed proposals sitting in
Studio today are proof that a human approval gate with a backlog protects nothing. What the
gate was protecting — *nothing is lost* — is bought by append-only writes, which already
exist, not by the click.

**4 · Reconcile template-to-template, not post-to-post.** No incremental per-post matcher.
The corpus fits in one call, so re-extract it and hand the model the families that already
have names. Reconciling ~15 templates is both cheaper and an easier judgement than
classifying 274 posts, and it is the comparison that can notice "these two are the same
shape" — which is how 42 hooks collapse.

**5 · Every non-retired family is offered for reconciliation, not just the approved ones.**

This decision was reversed during the build, and the first real run vindicated the
reversal — recorded here rather than quietly amended, because the original reasoning was
sound and it is worth knowing why it stopped applying.

The original decision offered only APPROVED families, on the grounds that a human had
vouched for those and not for the rest, and that showing the model 42 names would make it
cite them and preserve the explosion. The first half evaporated: the 9 APPROVED templates
turned out to have been approved by the model during a test run, so nobody had vouched for
anything and "approved" carried no signal. The second half is bounded by
`MAX_HOOK_PROPOSALS` — at most 10 proposals come out of a run whatever the model is shown,
so offering 41 families cannot produce 41 templates.

What offering all of them buys is repair. A legacy family with empty or single-post
provenance can be folded into a new version that cites real posts, keeping its
`family_id` and whatever lineage hangs off it. The alternative orphans it.

Measured on the first real run: 7 of 19 proposals folded into existing families rather
than minting siblings. Nothing is deleted either way — append-only, and `usable_templates`
already keeps PROPOSED rows out of generation.

**6 · Visuals out.** See the end.

---

## What gets built

### Sample

`_strongest_posts` gains a whole-corpus mode: every non-excluded post for the cohort and
platform, no `limit`, no `order_by`. Every existing exclusion stays — cohort separation,
`excluded_from_extraction`, `voice_since` on VOICE, non-empty content.

Hooks send 220 chars per post as today. Structures send full text.

### Prompt — new major version of `extraction.hooks` and `extraction.structures`

The instruction inverts from "abstract these posts" to:

> Find the patterns that **recur** across these posts. Return at most 10. For each, list
> every post id it covers. Here are the families that already have names — if one of your
> patterns is the same shape as an existing one, return its `family_id` rather than
> inventing a new name for it.

Passing the existing families in is the same mechanism `_structure_prompt` already uses to
let a structure cite a hook by name, pointed at the template's own kind.

`source_post_ids` becomes **required** in both schemas. For structures that is the fix for
the empty-provenance defect above.

### Write — reconcile instead of minting

- proposal cites a `family_id` that exists → `edit_template`, writing v+1
- otherwise → `create_template`, a genuinely new family

Each proposal is wrapped in its own `try/except → log → continue`, copied from
`propose_visuals` (`extraction.py:389-402`). Today `propose_hooks` ends in a bare list
comprehension and `_to_template` raises on a blank name, so **one malformed proposal out of
ten returns a 502 and writes nothing**. Visual extraction already got this right; the
comment there records that two narrower versions of the catch each cost the whole batch.

This catch is also all that is needed for a proposal citing a RETIRED family:
`edit_template` already raises `RetiredTemplateError`, so the proposal is logged and
dropped, and a family a human deliberately retired is never silently revived. No branch
required.

### Status

At write time, from the **filtered** provenance length:

- `PROPOSED` and `coverage >= 5` → promote to `APPROVED`
- `APPROVED` → never touched, whatever coverage does
- nothing is ever auto-demoted or auto-retired

The promote-on-a-later-run case is not optional. `edit_template` copies the status of the
row it revises (`templates.py:69`), so without an explicit promotion a family that lands at
coverage 3 stays PROPOSED forever — even when a later run finds it covering 40 posts.
Coverage growing as posts arrive is the entire point.

### Filter provenance before counting it

`_to_structure` passes the model's `source_post_ids` through unchecked; only `_to_template`
filters to ids the model was shown. That was harmless while structure provenance was always
empty. Once the schema requires the field, hallucinated ids land in provenance — and
provenance is now the approval gate, so five invented ids would auto-approve a template
covering nothing.

Not hypothetical: all three VISUAL provenance citations in the database today join to no
post row. Structures get the same filter hooks have, and **coverage is counted off the
filtered list**, so the gate measures the corpus rather than the model's confidence.

### UI

The extract buttons already exist (`TemplateManager.tsx:484-498`) and do not change — once
the sample widens, that button *is* "extract from every post". Per-post selection already
exists too, inverted: `excluded_from_extraction` with a toggle on every post detail page,
honoured at `extraction.py:96`. A standing exclusion beats a checkbox in a modal, because it
persists across every future run.

One addition: show the coverage count per template. `provenance` is already on the object
the API returns.

### Deletion

`sample_size` comes off `extract_structures`. Its docstring sells it as the way to reach
post types below the engagement cutoff; whole-corpus extraction makes it meaningless, and a
parameter that silently does nothing is exactly what this codebase's comments exist to
prevent. `focus` stays — naming a post type to describe is still a real request.

---

## Deliberately not built

| Skipped | Add when |
|---|---|
| Per-post incremental matcher | The corpus stops fitting in one call |
| Embeddings / clustering | An LLM call demonstrably mis-groups patterns |
| An automatic re-run trigger | Someone forgets the button and it costs something. `post` has no `created_at`, so "posts since last extraction" would need new state |
| An `unmatched` bucket in the schema | Something reads it. Derivable as `all_ids - union(provenance)` |
| Post-selection UI | Never — `excluded_from_extraction` already does this, and persists |
| Holdout validation (extract from 80%, check coverage on 20%) | The one-time read below stops being trusted |
| Any ranking or sort-by-performance control | Never. `Never rank` |

Hooks and structures stay two separate calls: structures take the hook list as input for
`compatible_hooks`, so they must run after hooks.

---

## The check that validates this

There is no ground truth, so this is not measured against the existing approved templates.

Run it once. Read the ~8-10 patterns with their coverage counts and post lists. Open three
posts a pattern claims to cover and ask specifically whether **the pattern describes those
posts** — not whether it is a sensible pattern in the abstract.

That distinction is the point. "At most 10 patterns" and "cover 274 posts" pull against each
other; the cheap failure is patterns that cover very little, the expensive one is a pattern
stretched loosely over 30 posts that reads as high coverage. Only reading the posts tells
them apart.

`5` is a guess. This read is where it gets earned, and it may well move.

The remaining unknown is whether a model stays honest labelling 274 posts in one call.
Output is only ~7k chars, so truncation is not the risk — attention is. Same read catches it.

---

## Why visuals are out

Coverage is the load-bearing idea here, and the image corpus cannot support it:

```
posts with a still image:  Monte 32/236 · inspiration 12/15 · pixii.creates 13/13 · Pixii_ai 2/10
```

59 images total. A coverage count of 4 out of 32 does not mean what 40 out of 274 means, and
every sample goes into the request as a real image, so "read the whole corpus in one call" is
a completely different cost and reliability story.

Visual extraction is also the healthiest of the three already: it has per-proposal rejection,
and a render gate that proves a layout can be drawn before a human ever sees it. It keeps its
top-5 sample and its human approval, and gets revisited as its own effort.
