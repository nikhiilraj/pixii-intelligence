# V2 — handoff and run record

Written at the close of US-018, the last slice of V2. **Assume the conversation that produced V2
is gone.** This file is what remains: what the app is, how to run it, what shipped, what did not,
and the one thing that has to happen next which no amount of code can do.

Written 2026-07-30. Every number below was read from the live database or a live test run on that
date, not carried over from a plan.

---

## What V2 is

Pixii Intelligence is a single-operator tool for Monte's LinkedIn: it holds a corpus of published
posts, extracts reusable templates (hooks, structures, visuals) from them, generates drafts in that
voice, pushes them to Zernio as **drafts only**, notices when one goes live, joins analytics back to
the template that produced it, and records a human verdict.

That chain is **the lineage circuit**, and V2's entire purpose was to make one lap of it possible.
V1 could generate and push. It could not put a real image into a visual template, could not tell
that a pushed draft had gone live, had nowhere to record a judgement, and had no page that showed
what was waiting on a human. V2 closed those four gaps.

**What it is not:** an optimizer. Nothing in this app ranks templates against one another, and that
is a design constraint, not a missing feature — see "The optimizer" below. It is a scoreboard.

## The three tracks

V2 ran as 18 slices in three tracks. Full per-slice detail is in `progress.txt` under
`## Session log`; do not re-derive it from the code.

- **Track A — Asset Library (US-005…US-010).** An `image_url` template slot had no source, so a
  visual template containing an image could not complete. Now: an `asset` table, upload and
  promote-from-corpus, a single `resolve_asset_values` that all three render paths go through,
  base64 data-URI embedding, a delete guarded 409-if-referenced, and a picker in Studio. Proven live
  against real Cloudflare in US-009 — a 1080×1350 PNG containing two real images, not empty boxes.
- **Track B — Loop Surface (US-011…US-015).** `Draft.went_live_at` plus a publish-detection pass
  that is deliberately **not** limited to Zernio's 50-row analytics window; `Post.verdict` /
  `verdict_note` / `verdict_at`; `GET /inbox` returning the four human-gate queues; the Inbox as the
  home page; and recorded verdicts feeding back into generation as lessons.
- **Track C — Design System (US-001…US-004 as "C-thin", then US-016…US-018 as polish).** Tailwind v4
  `@theme` tokens on the real brand palette, presentational and Radix primitives, an **honest API
  layer** (`ApiResult<T>` — a network error, a 500 and a legitimately empty result are now three
  different things, where before all three collapsed to `null`), loading/empty/error states on every
  route, a motion and accessibility sweep, and this slice's IA collapse.

A frontend test harness (Vitest + Testing Library + jsdom) landed mid-run in US-003, because six V2
slices carried "asserted" criteria that were vacuous without it. The policy that governs it is in
`progress.txt` under **"Frontend testing policy (decided 2026-07-30, Nikhil)"** and is binding: assert
our own logic, never Radix's behaviour, never claim a contrast ratio in a test.

## How to run it

```
make up      # Postgres in Docker on :5433 (waits for pg_isready)
make api     # runs migrations, then uvicorn on :8000
make web     # Next.js dev server on :3000
```

`make check` = `lint` (ruff, mypy, eslint, tsc) + backend pytest. Frontend tests are
`cd frontend && pnpm test`; the production build is `pnpm build`.

Credentials live in `.env` and are never committed. Key **names** only: `ZERNIO_API_KEY`,
`AZURE_OPENAI_*` (chat and image deployments), `CLOUDFLARE_*` (account id and Browser Rendering
token). `GET /health` reports which are present without revealing any value; those rows render in
the Inbox footer.

## Test counts at the close of V2

| | count | how |
|---|---|---|
| Backend | **399** passing | `make check` |
| Frontend | **62** passing across 7 files | `cd frontend && pnpm test` |
| Build | clean, **7 routes** | `pnpm build` |

There are **zero scheduler tests** — tests call `sync_metrics(...)` and `stamp_published(...)`
directly, so no test exercises the APScheduler job wrapper. Time is never faked anywhere in the
suite: no freezegun, no patched clock.

## Authoritative files

| file | what it is |
|---|---|
| `.scratch/v2/prd.json` | the 18 slice contracts and their pass state. **The master session owns it.** |
| `progress.txt` | the run log. `## Codebase Patterns` at the top is verified ground truth (the Zernio API section says "do not re-derive" and means it); `## Session log` is one entry per slice. |
| `.scratch/v2/seams.md` | seams verified against the live tree and live APIs **before** slicing, with a legend for what was confirmed, corrected and newly discovered. Read this before trusting the PRD on any technical point. |
| `.scratch/v2/PRD.md` | the plan. Superseded by `seams.md` and `progress.txt` wherever they disagree — several of its claims were corrected during the run (the accent colour, the Cloudflare payload ceiling, the "all 37 proposals are stale" recommendation, the "status colours are all AA" claim). |

There is also a **stale `prd.json` at the repo root** — V1's. `.scratch/v2/prd.json` is the V2 one.

---

## The exit criterion — NOT MET

**V2's exit is operational, not technical.** All 18 slices can pass and V2 still has not done what it
was for. The criterion is **one closed circuit**: a generated draft published, its metrics joined
back through lineage, a verdict recorded, and the scoreboard's first non-zero row.

**That has not happened.** Verified live 2026-07-30 against the real database:

```
GET /inbox
  proposals_awaiting_review    37
  built_awaiting_push           4
  pushed_awaiting_monte         2
  published_awaiting_verdict    0     <- the circuit has never run a lap
```

`/scoreboard` renders, against the real 50-row template library: *"No generated post has been
published yet — the scoreboard starts with the first circuit."* Every version is listed at a sample
count of zero.

Queue 4 is zero because **no generated draft has ever been published** — `draft.went_live_at IS NULL`
for all 6 rows. Publishing is a human act performed in Zernio's own dashboard; this app can only
notice that it happened. Software has made that lap cheap; **Monte publishing is the throughput
limit and no slice can move it.**

Read the two pushed drafts before assuming queue 3 is a real pending publish:

| draft | Zernio id | note |
|---|---|---|
| 48 | `6a691c6d95e614b6077edb22` | PRD.md:411 records this same id as a **test draft awaiting deletion**. Whether it is the one to publish is Monte's call, not an assumption to inherit. |
| 555 | `6a695286ead2fabfa56f3c27` | idea reads *"about cat on moon hypothesis"* — a probe, not content. |

So the honest state is: the machinery is built and proven in parts, and **the first real lap has not
been attempted.** Do not write V2 up as if the loop has closed.

---

## Deferred, with the trigger that reopens it

Truncation must not read as completeness. Each of these was a decision, not an oversight.

### Cut from this run

**Impressions CSV import.** Cut because its parser cannot be verified against a file nobody has
seen — a column-mapping guess would ship as tested code and be wrong on first contact.
**Trigger: Monte's analytics export arrives.**
*Consequence today:* the 35 scraped `MANUAL / Monte Desai` rows all carry `impressions = 0`, so
**`total_impressions` on the scoreboard is a lower bound, not a measurement.** Engaged actions are
unaffected. When the import runs, `max()` semantics should make it non-destructive — eyeball the
before/after on 3 rows the first time (PRD "Needs Nikhil's Real Eyes" #6).

**~25 posts, 2025-08 → 2026-01.** On Monte's profile, in neither file nor database: LinkedIn
rate-limited the scroll. The scraped range is 2024-01-25 → 2026-07-17 with that gap inside it.
**Trigger (PRD.md:400): only if extraction quality is ever the suspect** — the window is mostly
pre-`voice_since`, so it is largely outside the voice sample anyway.

**The optimizer.** Never a backlog item — **a threshold: ~300 lineage-tagged posts.** Today there are
zero. Ranking 50 template versions at ~3 samples each, across a 12.7× engagement spread, would be
presenting noise as a finding. Every "not ranked" comment in the UI is load-bearing; a future slice
that adds a sort control to the scoreboard is undoing a decision, not adding a feature.

**Channels and inputs, with the PRD's own triggers (PRD.md:389-400):**

| cut | trigger to reopen |
|---|---|
| X / Reddit / other channels | ≥10 closed circuits on LinkedIn |
| Comment mining | autonomous mode on **and** the draft queue running dry |
| Creator watch | proposal review debt at zero **and** recurring creator material |
| Video pipeline | a decision to retire podcast-chopper |
| Per-platform `voice_account` mapping | falls out of the channel trigger above |
| Object storage for media/assets | the app runs on more than one machine |
| Comment-to-DM automation | a separate product decision (supersedes LinkedIn-Offer-Reward) |
| Multi-user / auth / roles | a second regular user exists |

### Known and left unfixed

**`AzureImageRenderer` carries the same 429 exposure Cloudflare had.** US-007 gave
`CloudflareRenderer.screenshot` a bounded 429 retry (4 attempts, 5/10/20s = 35s). The Azure path did
**not** get one: `rendering.py:211` turns any status ≥ 400, 429 included, into `ImageGenerationError`
with no retry. Left alone because Azure image calls are made manually, one at a time, so a cadence
limit is unlikely. **Trigger: any batch or autonomous path that generates more than one AI visual per
minute** — at which point a rate-limited call is also a wasted paid request.

**Animated GIFs flatten to one frame** if they ever exceed the 1600px downscale ceiling — Pillow's
resize-and-save drops every frame but the first, **silently**. Two animated GIFs are in the corpus
(6.6MB and 7.2MB); **neither is currently affected** because both sit under the ceiling and pass
through unchanged. No animated-image handling exists anywhere.
**Trigger: raising the 1600px ceiling, or uploading a large animation.**

### Human gates, not code

**37 template proposals await review.** They are **not** all stale — the PRD recommended bulk-retiring
them and Postgres refuted it: 9 (2026-07-28) predate the corpus widening and are stale, but **28
(2026-07-29) post-date it**, including the entire 10-proposal creator-inspiration cohort. A bulk
retire discards the re-extraction, not the stale batch. Review them; don't sweep them.

**Draft 48 awaits Monte publishing it** — see the exit-criterion table above for why that draft in
particular deserves a second look first.

Neither is a code task. Both block the exit criterion.

---

## What US-018 itself changed

**Navigation is five destinations**, in the order a post moves through them:
`Inbox` (`/`) · `Studio` (`/studio`) · `Corpus` (`/posts`) · `Templates` (`/templates`) ·
`Assets` (`/assets`).

Removed from the nav: **Scoreboard**. It is not orphaned — it hangs off the Templates page lede, and
`scoreboard/page.tsx` already links back to Templates from its empty state, so the pair is
bidirectional. It was demoted because it reads `/metrics/templates` (it is that library's evidence,
not a sixth area of the app) and because nav-level billing implies a page where you go to decide
something; at zero attributed posts it is where you go to see that nothing has been decided.

**PRD.md:381's IA line lists six routes plus `/posts/[id]` — that is a route inventory, not a nav
spec.** Seven routes exist; five are destinations. There is no conflict with prd.json's "exactly the
five destinations", and Scoreboard should not be restored to the nav on the strength of that line.

Route inventory, every one with a named inbound link:

| route | reachable from |
|---|---|
| `/` | nav |
| `/studio` | nav; Inbox queues 2 and 3 |
| `/posts` | nav; every scoreboard row (`?template_family=`) |
| `/posts/[id]` | `posts/Explorer.tsx:364`; Inbox queue 4 |
| `/templates` | nav; Inbox queue 1; scoreboard's library-empty state |
| `/assets` | nav |
| `/scoreboard` | Templates page lede — **its only inbound link** |

**Shell width moved to `max-w-6xl`, and it had to be all of it.** The nav sat at `max-w-5xl` while
`posts/`, `studio/` and `assets/` rendered at `6xl`, so on exactly the table pages the width exists
for, the nav's first link began 4rem inboard of the content beneath it. Widening only the nav would
have relocated that 64px seam onto the other three pages rather than closing it — and "the shell at
`max-w-6xl`" cannot mean a width that applies only to the chrome. So all eight containers moved:
`layout.tsx`, and `(inbox)`/`templates`/`scoreboard` pages **with their `loading.tsx` twins** (a
skeleton at a different width than its page is a visible layout jump — the class of defect US-016
existed to remove). Prose is capped at `max-w-2xl` independently, so nothing over-widened.
`/posts/[id]` and `route-error.tsx` stay at `max-w-3xl` on purpose: a single post is a reading
measure, not a table, and each agrees with its own skeleton.

**No test asserts nav contents** — `pnpm test` reports 62 whether the nav is right or wrong. This
slice's nav claim was verified by grep plus a live render (`curl` against `next dev`, which returned
exactly the five destinations and the Scoreboard link on `/templates`), not by a test.

### Follow-ups spotted while here and deliberately not done

- `templates/page.tsx` was still on raw `text-2xl` / `text-sm opacity-60` rather than the US-001
  tokens; converted to `text-title` / `text-body text-muted` **only because the link went into that
  same paragraph** and a 60%-opacity link is an accessibility problem. Other pre-token holdouts, if
  any remain, were left alone.
- `TemplateManager.tsx:291` carries a hardcoded `border-black/10 dark:border-white/15` instead of
  `border-border`. Cosmetic, untouched.
- Studio holds one in-session draft in local state and cannot load an existing one, so both Inbox
  draft queues link to bare `/studio`. `GET /drafts/{id}` already exists; the ceiling is a `?draft=`
  param read in `studio/page.tsx`.
- Inbox queue 3 has no honest external link — `InboxItem` carries no `zernio_post_id`, and a
  synthesized Zernio dashboard URL would be a guess that looks like a fact.

---

## Before touching anything, read these

Three traps that cost time during V2 and will cost it again:

1. **`POST /drafts/autonomous-run` ignores `enable_autonomous`** (`api_drafts.py:213`). That setting
   gates only the *scheduled* job, so the endpoint runs a full generation pass on demand — spending
   Azure image credits, and **there is no budget cap or spend counter anywhere in the app**. Insist on
   a cap before autonomous mode is ever switched on.
2. **Every datetime column is `timestamp without time zone`.** A value written as
   `datetime.now(UTC)` reads back **naive** after a round-trip, depending on when the ORM expired the
   object — so one query can yield both aware and naive datetimes, and comparing them raises.
   Normalised in `main._utc`.
3. **Base64 is load-bearing for asset embedding** (`assets.py:168`). `rendering.fill` HTML-escapes
   slot values because they come from a language model; base64's alphabet happens to dodge all five
   escaped characters, but a raw `data:image/svg+xml,<svg…>` is mangled to `&lt;svg` and renders
   broken **with no exception raised**. Do not "fix" this with `escape=False` — that flag is the
   injection guard.

---

## G2 review findings — added 2026-07-30 after two adversarial review passes

Two reviewers (Standards axis, Spec axis) went over the whole branch against the PRD and the
repo's own rules. **They found things the per-slice verification did not** — worth knowing that
per-slice checking of invariants is not a substitute for walking each acceptance criterion.

### Fixed before merge
1. **The verdict UI did not exist.** Backend route, columns, 500-char cap, six tests and the
   lessons feedback path were all real, but no form existed on `/posts/[id]` and `Post` in
   `lib/api.ts` had no verdict fields — so Inbox queue 4 linked to a page that could not clear it,
   and "know what worked and what didn't" had no human input. US-014 was marked passing with its
   "every item links to the page that clears it" criterion unmet.
2. **`cap` on `POST /drafts/autonomous-run` had no ceiling** (`api_drafts.py:245`).
   `autonomous_max_drafts = 2` was a default, never a bound, while `run_autonomous`'s docstring
   asserted "`cap` is hard". `?cap=500` meant up to **1001 billed Azure chat completions**, 250x
   the configured cap, with no spend counter anywhere. (This path uses only `html_renderer`, so it
   could not reach Azure *image* generation.)
3. **`regenerate_visual` resolved the template by latest version, not the draft's own**
   (`generation.py:424`), while `_image_slot_names` (`assets.py:313`) correctly keys on
   `(family_id, version)`. Redrawing a v2 draft after a v3 existed stored a v3 image while
   `visual_version` still said 2 — so Zernio metadata and `template_performance` both credited v2
   with v3's picture. A silent attribution error in the one thing this app exists to record.

### Accepted as follow-up — NOT fixed, do not read the above as "all clear"
| Item | Where | Note |
|---|---|---|
| Circuit counter ("Closed circuits: N") | PRD.md:209 marked **Build** | silently dropped; the Inbox has no counter |
| Engagement curve + draft lineage on post detail | PRD.md:186; `/posts/{id}/history` + `draft_for_post` exist | routes render nowhere |
| Lessons in `autonomous.propose_topics` | PRD.md:132 | `verdict_lessons` reaches only `generate_draft` and `regenerate_text` |
| `DecompressionBombError` escapes `store_image` | `assets.py:114` | a 68-byte PNG claiming 30000x30000 returns **500**, not the documented 422. Verified empirically |
| recharts never moved onto tokens | `Explorer.tsx:336` hardcodes `#F2610C`; `:326` uses opacity-as-muted | `--chart-1..5` declared and consumed by nothing; the AA pattern PRD.md:373 names as failing |
| `Card` exists but 6 pasted card strings survive | `Studio.tsx:77,203,330`, `posts/[id]/page.tsx:15`, `ExcludeToggle.tsx:33`, `TemplateManager.tsx:233` | US-002's "exactly one place" criterion literally unmet |
| Inbox queue 4 is lineage-only | `main.py:437` | reasoned (57 published posts carry no verdict and would flood it), but those 57 have no surface at all |
| Asset picker is a `Select` per slot | `Studio.tsx:198` | PRD.md:167 specified a dialog with grid, search and upload-in-place; not arrow-key navigable |
| `{verdict: null}` cannot clear a verdict | `main.py:272` `VerdictIn.verdict` is required | PRD.md:178 said it could |
| `HealthRow` API row can only render green | `(inbox)/page.tsx:196` | inside the `health.ok` branch; ignores `health.data.status` |
| The dead `note` param | `main.py:164` | the cut list promised to delete it |
| `/posts/[id]` is a pre-token holdout | | may read as a different app |

### The honest state of "best UX and UI"
**No page in this application was ever seen in a browser during the build.** The Chrome extension
was unresponsive for the whole run (5 attempts). What is verified: tokens, measured contrast
ratios, one global focus ring, one reduced-motion query, loading+error boundaries on all 7 routes,
and Vitest coverage of our own logic. What is **not** verified: real layout at any viewport, dark
mode, whether cream-on-white reads as hierarchy, Radix overlay positioning, the assets grid at 2
rows versus 200, and how `/posts/[id]`'s untokenised styling sits beside the rest. Two a11y
criteria rest on hand-verification claims that name nothing specific.

**A browser pass over all seven routes in both themes is the right next step before Track C is
called done.**
