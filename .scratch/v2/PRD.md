# Pixii Intelligence — V2 PRD

**Status:** aligned — building
**Date:** 2026-07-30

> **Build-run decisions (2026-07-30, Nikhil).** Amendments to this PRD from the ship-it ALIGN step:
> 1. **Track order: thin C → A → B → C polish.** The component layer ships first as prefactoring
>    so Track B's four queue views are built once, on the tokens, instead of reskinned later.
> 2. **Component layer: vendor the shadcn subset** as specified — Radix primitives +
>    cva/clsx/tailwind-merge + sonner. The only dependency addition in V2; approved on the
>    a11y-is-a-never-simplify-item reasoning.
> 3. **Impressions CSV import: DEFERRED out of this run.** Its parser cannot be verified against
>    a file nobody has seen, and a slice that reports green on guessed columns is worse than an
>    absent one. Trigger: Monte's export actually arrives. The 35 rows with `impressions=0`
>    remain a documented lower bound until then.
**Repo:** `~/pixii-intelligence`
**Predecessor:** `.scratch/v1/spec.md` (V1 shipped: 15/15 slices, 258 tests green, 107 posts persisted)

---

## The Decision

**V2 is the last planned version. There is no V3 queue.** The 10x of a scoreboard-with-lineage
is not more features — it is a loop that actually turns. V1 built every mechanical part of the
circuit (corpus → templates → generation → draft → sync → scoreboard) and the circuit has
completed **zero** laps: two drafts sit unpublished in Zernio, 37 proposed templates sit
unreviewed, and the scoreboard is honestly all zeros. The bottlenecks are (a) a template
system that cannot complete any visual with an image in it, (b) human gates that are invisible
until someone remembers them, and (c) a UI that gives no reason to open the app daily. Nothing
on the old 8-item roadmap fixes any of those; most of it widens a pipe that has never carried
water.

So V2 is three tracks against those three bottlenecks, plus a hard cut of everything else:

- **Track A — Asset Library.** The gating dependency of the templates pillar. `image_url`
  slots get a real source, so visual templates complete instead of silently rendering empty
  boxes. This is what makes "recreate this template with another topic" true instead of
  aspirational.
- **Track B — The Loop Surface.** An Inbox that makes every human gate visible and cheap
  (proposals to review, drafts to push, pushed-awaiting-publish, published-awaiting-verdict);
  automatic detection of the moment a pushed draft is published; and **verdicts** — a
  one-line human ruling per post ("worked / didn't / mixed" + why) that is the only form of
  learning that is statistically honest at n=1, and which feeds back into generation as
  explicit lessons. This is "everything on record — what worked and what didn't" in Nikhil's
  own words, built for the corpus we have rather than the corpus we wish we had.
- **Track C — Design System.** Greenfield: `globals.css` is 26 lines of create-next-app
  boilerplate with `font-family: Arial`. Tokens, type scale, motion, component layer,
  loading/empty/error states, keyboard and a11y — concrete spec below.

**Architecture: unchanged.** FastAPI + SQLModel + Alembic + Postgres :5433; Next.js 16 +
React 19 + Tailwind v4 + Recharts. Same adapters (`zernio.py`, `llm.py`, `rendering.py`),
same test posture (`httpx.MockTransport`, no live calls), same scheduler (APScheduler
in-process). Every V2 capability lands as one new module + one router + additive migrations.
Rejected: any queue/broker (two periodic jobs don't need one), any vector store ("semantic
search over 107 posts" is a `WHERE ILIKE`), any new SaaS (constraint 2), object storage
(one machine; `media/` already serves files).

**The exit criterion for V2 is operational, not technical:** one full circuit closed — a
generated draft published by Monte, its metrics joined, a verdict recorded, the scoreboard's
first non-zero row. The software can only make that cheap; Monte publishing remains the
throughput limit, and V2's Inbox is designed around that fact instead of pretending compute
is the constraint.

Added recurring cost: **$0** (local disk, existing Zernio/Azure/Cloudflare).

---

## Data Model

All changes **additive**. Three Alembic migrations, no destructive operation anywhere.

### New table: `asset` (migration 1)

`backend/app/models/asset.py`

| column | type | notes |
|---|---|---|
| `id` | int PK | |
| `filename` | str | file under `media/assets/`, served by the existing `/media` mount |
| `label` | str | human name, searchable |
| `kind` | StrEnum `AssetKind` | `logo` \| `product` \| `screenshot` \| `brand` \| `photo` |
| `tags` | JSONB list[str] | free tags for filtering |
| `width`, `height` | int | read at upload with PIL (already a dependency) |
| `sha256` | str, unique | dedupe — re-uploading the same file returns the existing row |
| `source_post_id` | int FK post.id, nullable | set when promoted from corpus media |
| `created_at` | datetime | |

Files live at `media/assets/<sha256-prefix>_<safe-name>` — gitignored like the rest of
`media/`. On upload, anything larger than 1600px on its long edge is resized down with PIL
(assets are composited into 1080×1350 frames; larger is waste and threatens the render
payload limit — see Open Questions).

### `post` (migration 2)

| new column | type | notes |
|---|---|---|
| `verdict` | str nullable | `worked` \| `didnt` \| `mixed` — a StrEnum in code |
| `verdict_note` | str default `""` | the why, ≤ 500 chars enforced at the route |
| `verdict_at` | datetime nullable | |

ponytail: columns, not a table — the real cardinality is one ruling per post. A verdict
history has no user until two people use this app.

### `draft` (migration 3)

| new column | type | notes |
|---|---|---|
| `asset_values` | JSONB default `{}` | `{slot_name: asset_id}` — the visual's asset lineage |
| `published_at` | datetime nullable | stamped by the sync when the joined post goes live |

Slot definitions inside `Template.slots` (JSONB, no migration) gain an optional
`default_asset_id: int` so a template can name its own logo/product cutout and complete
unattended.

`publishing.lineage_metadata` adds `asset_values` to the metadata pushed to Zernio, so the
full lineage (templates **and** assets) survives outside our database, as template lineage
already does.

---

## The Five Pillars

### 1. Create content easily from past learnings

**Exists:** exemplar-grounded generation (`generation._exemplars`, hard-restricted to
`settings.voice_account`), suggest→override flow, regenerate text/visual independently.

**V2 adds — lessons in the prompt.** `generation._write_prompt` gains a `lessons` block:
the most recent N (=8) verdicts on `voice`-cohort LinkedIn posts, each rendered as one line —
`WORKED: <note>` / `DIDN'T: <note>` — truncated to 200 chars each. Human-authored judgments
are the one "learning from past posts" that is honest at current n; the statistical kind
waits for the thresholds table. Also: `autonomous.propose_topics` gets the same block, so
unattended topics avoid what demonstrably didn't work (e.g. the corpus's own finding:
research-shaped posts are its weakest content — once Nikhil records that as verdicts, the
generator is told).

Surface: `backend/app/generation.py` (one new query + prompt section), tests in
`test_generation.py` asserting lessons appear and are bounded.
**Gating dependency:** verdicts existing at all → Track B.

### 2. Hooks / structures / visuals as re-topicable templates

**Exists:** versioned template families, approve/retire lifecycle, extraction from both
cohorts, two renderers, slot typing with the `WRITABLE_SLOT_TYPES = {"text","number"}` guard.

**V2 adds — the Asset Library**, the actual blocker (stat-hero v1 was retired for it):

- `backend/app/assets.py` — store/list/dedupe/resize/promote-from-post.
- `backend/app/api_assets.py` — router `/assets`:
  - `POST /assets` (multipart: file + label + kind + tags) → 201, dedupes on sha256
  - `GET /assets?kind=&q=` — q matches label/tags
  - `PATCH /assets/{id}` — label/kind/tags only
  - `DELETE /assets/{id}` — **409 if any `draft.asset_values` references it**; deletes the
    file too (flagged in Needs-Real-Eyes)
  - `POST /assets/from-post/{post_id}` — promotes a corpus post's `local_media_path` into
    the library (copy, not move)
- **Resolution path:** `image_url` slots are *never* LLM-writable — `WRITABLE_SLOT_TYPES`
  is untouched. They resolve, in order: caller-supplied `asset_values` → the slot's
  `default_asset_id` → **fail loud** (`MissingSlotValue`, which the existing render path
  already surfaces as `visual_error`). Resolution turns an asset into a `data:` URI embedded
  in the HTML before `rendering.fill()` — Cloudflare Browser Rendering is a remote service
  and can never fetch `localhost` media (see Open Questions for the payload-size check).
- `POST /drafts` and the regenerate-visual route accept `asset_values: dict[str, int]`.
- Autonomous runs only select visual templates whose non-writable slots all carry defaults —
  an unattended run must not be able to produce a knowingly-incomplete visual.

Frontend: `AssetPicker` dialog in Studio (grid, search, upload-in-place), `/assets` page
(library management), thumbnails via the existing `/media` mount.

**Gating dependency:** none — this *is* the gate for everything visual.

### 3. Everything on record — what worked and what didn't

**Exists:** full corpus persisted (107 posts, 4 sources), metric snapshots, lineage in two
places, `excluded_from_extraction` with reasoned holdouts.

**V2 adds:**
- **Verdicts** — `POST /posts/{id}/verdict` (`{verdict, note}` or `{verdict: null}` to
  clear), UI on the post detail page. The record of *why*, which no metric carries.
- **Impressions truth** — `POST /corpus/impressions-csv`: Monte's LinkedIn analytics export,
  saved as CSV, joined on the proven normalized-content key (`corpus._content_key` — the
  URN join is known-broken across namespaces), applying `max(observed, stored)` semantics
  exactly like `corpus._apply`. This is the *only* route to impressions on the 35 scraped
  rows (all currently 0, making `total_impressions` a lower bound). Stdlib `csv`, no new
  dependency; XLSX is refused — export as CSV.
- **Engagement curve** — the post detail page charts `GET /posts/{id}/history` (endpoint
  exists, is currently rendered nowhere) with Recharts, and shows draft lineage when the
  post came from this app (`metrics.draft_for_post` exists, also rendered nowhere).

### 4. The improvement loop

**Exists mechanically, has never turned.** The loop's real shape is:
idea → draft → human review → push → **Monte publishes** → sync joins → scoreboard + verdict
→ lessons feed the next idea. Two segments are human gates; V2 makes them visible and cheap
rather than pretending to automate them away.

**V2 adds:**
- **Inbox as the home page** (`/` — replaces the Status page; health folds into a footer
  strip). Four queues, each with count, age, and one-click-through:
  1. Template proposals awaiting review (37 today) — approve/retire inline
  2. Drafts generated, not yet pushed
  3. Pushed, awaiting publish (with days-waiting — this is the Monte gate, made visible)
  4. Published, awaiting verdict
- **Publish detection** — `metrics.sync_metrics` gains a post-pass: any `Draft` with a
  `zernio_post_id`, no `published_at`, whose joined `Post` (via the verified
  `Draft.zernio_post_id == Post.late_post_id` key) is `status == "published"` gets
  `published_at` stamped and fires `notify()` ("the loop closed on draft N"). The existing
  6-hour schedule is the cadence; no new job.
- **Circuit counter** — the Inbox header states plainly: "Closed circuits: N" with N=0
  rendered honestly, not hidden. The empty state *is* the call to action.

Routes: `GET /inbox` (one aggregate endpoint returning the four queues), no new models —
every queue is a query over existing tables + the two new columns.
**Gating dependency:** Monte publishing draft 48. Software cannot close this; it can only
stop it being forgotten.

### 5. Senior-grade UX/UI

Greenfield — full spec in **Design System** below. Every page rebuilt on the token +
component layer; Studio redesigned around the real flow (idea → templates → draft → assets →
push) with the AssetPicker; every route gets `loading.tsx` / `error.tsx` / designed empty
states; keyboard and screen-reader coverage.
**Gating dependency:** none. Track C can start immediately and everything else lands on it.

---

## Trust Thresholds

The most load-bearing table in this document. Rule: **any feature that ranks, scores,
selects, or recommends must state the n at which it is trustworthy and whether the corpus is
there.** Corpus today: 107 posts total, 69 voice-cohort, **0 lineage-attributed** (no
generated post has ever been published). Engagement spread 12.7x (median 525, max 6691).

| Feature | Nature | Trustworthy at | Corpus there? | V2 ruling |
|---|---|---|---|---|
| Scoreboard aggregates with sample count | descriptive | readable at any n; flagged `insufficient` below `min_sample_size=5` | 0 attributed | **Keep** exactly as V1 built it — display, never rank |
| Comparing/ranking template A vs B | inferential | ~30 attributed posts *per template version* (12.7x spread means fewer cannot separate a good template from a lucky one) | 0 | **Not built.** No sort-by-performance on the scoreboard |
| Auto-selection / bandit / optimizer | inferential | ~300 lineage-tagged posts corpus-wide | 0 | **Cut** (see cut list). A threshold, not a backlog item |
| `suggest_templates` (LLM picks for an idea) | fit judgment, not statistics | any n — but it must be labeled *fit*, never "best performing" | n/a | **Keep**; UI copy audited to never imply performance |
| Extraction's top-12-by-engaged-actions sampling | evidence selection | adequate at 69 voice posts; guarded by `excluded_from_extraction` holdouts | yes | **Keep** |
| Exemplar pick (top-3 of a hook's provenance) | evidence selection | adequate; hard-restricted to voice account | yes | **Keep** |
| Verdicts / lessons in prompt | human judgment | n=1 by construction | yes (once recorded) | **Build** — the only new "learning" V2 ships |
| Engagement curve, Inbox ages, circuit count | descriptive | any n | yes | **Build** |

The system's honest posture, unchanged from V1: *record now, rank later.* V2 widens what is
recorded (assets, verdicts, publishes, impressions); it adds **zero** inferential features.

---

## The Cut List

Governing rule: **every addition names a deletion or deferral.** Ledger first, then rulings.

| Addition | Pays for it |
|---|---|
| Asset Library (Track A) | X/Reddit channel expansion — cut |
| Loop Surface (Track B) | Comment mining + creator watch — cut |
| Design System (Track C) | Status page deleted as a destination (folds into Inbox footer); old five-page IA collapses to Inbox / Studio / Corpus / Templates / Assets |
| Impressions CSV import | the dead `note` parameter plumbing in `add_manual_post` / `ManualPostIn` (already ponytail-flagged for deletion) — deleted in the same change |

### Rulings — the 8 roadmap items

1. **Widen corpus** — **DONE** (shipped 2026-07-30: 107 posts, all persisted). Closed.
2. **Asset picker** — **IN, promoted.** It is Track A, the centerpiece — the gating
   dependency of the templates pillar, not a backlog item.
3. **Channels beyond LinkedIn (X/Reddit)** — **CUT.** The loop has closed zero times on one
   channel; multiplying channels multiplies drafts nobody publishes and forces the
   `voice_account` per-platform refactor for no return. Trigger to revisit: ≥10 closed
   circuits on LinkedIn.
4. **Comment mining** — **CUT.** Topic supply is not the bottleneck — publishing is. The
   autonomous topic proposer already exists and is barely used. Trigger: `enable_autonomous`
   on and the draft queue actually running dry.
5. **Creator watch** — **CUT.** 37 proposals already await human review; automating proposal
   *creation* grows the exact backlog that is stuck. Manual inspiration ingest exists and
   works. Trigger: review debt at zero and fresh creator material arriving faster than a
   paste can keep up with.
6. **Video path** — **CUT from this app.** A different product with different ingest,
   storage, and review; `podcast-chopper` continues to own it. Trigger: a deliberate
   decision to retire podcast-chopper, which is a vault decision, not a feature here.
7. **Retire old tools** — **MERGED into V2's exit criteria.** When one circuit closes,
   `pixii-cli`'s content path is declared superseded in [[Tools-Catalog]] — a decision and a
   vault note, zero code. `ai-slop-scorer` stays reachable behind its adapter seam as V1
   decided.
8. **Optimizer** — **CUT, permanently as a roadmap item.** It is a *threshold*: ~300
   lineage-tagged posts. It re-enters by the corpus earning it, not by scheduling it.

### Rulings — the 3 deferred items

- **Monte's analytics export** — **IN** (small): the impressions CSV route above. One
  endpoint, stdlib csv, proven content-key join, `max()` semantics. It repairs a known
  falsehood in the data (35 rows with impressions=0).
- **Asset picker** — **IN** (same ruling as #2).
- **Optimizer** — **CUT** (same ruling as #8).

### "Versions" — both readings, ruled

- **The roadmap ladder (V2→V3→V4→V5): collapsed to one shipping target — V2.** After V2,
  there is no version queue; there is the *What Stays Out* table below, each row with the
  trigger that would bring it back. Work re-enters by trigger, not by ordinal.
- **Template versions (retired v1s, e.g. stat-hero v1): pruning REFUSED.** The version
  history *is* the attribution record — deleting rows is destructive with zero payoff, and
  retired versions are already invisible to generation (`usable_templates` filters them).
  The only version hygiene V2 ships is cosmetic: the Templates page collapses old versions
  behind a disclosure instead of listing them flat.

---

## Design System Spec

Baseline: `frontend/src/app/globals.css` is untouched boilerplate; Geist is loaded in
`layout.tsx` but the body overrides it with Arial. Treat as greenfield.

**Component strategy:** vendor a shadcn/ui subset (Button, Select, Dialog, Tabs, Table,
Badge, Skeleton, Sonner/toast, Tooltip) into `frontend/src/components/ui/` — copied source,
not a runtime SaaS; the only new packages are Radix primitives + `class-variance-authority`/
`clsx`/`tailwind-merge`. Justified against ponytail because accessible Select/Dialog behavior
(focus trap, typeahead, ARIA) is precisely the thing not to hand-roll. Everything else is
hand-written on the tokens.

### Tokens (`globals.css`, `@theme`)

Brand: post visuals are cream `#F5F0E8`, heavy black type, orange Pixii wordmark. The UI
takes the same temperature without cosplaying as a post.

```
Light:  --bg #FAFAF7   --surface #FFFFFF  --surface-2 #F5F0E8 (brand cream, raised cards)
        --border #E4E1D8  --text #191917  --text-muted #6F6C64
Dark:   --bg #121210   --surface #1B1B18  --surface-2 #232320
        --border #34342E  --text #EDEBE4  --text-muted #98958A
Accent: --accent — Pixii orange, sampled from the wordmark asset (placeholder #E8590C
        until sampled; see Open Questions). --accent-fg #FFFFFF.
Status: --success #2F9E44  --warning #E8A33D  --danger #D6455D — all AA on both surfaces.
Charts: 5-step categorical anchored on accent + desaturated ink steps; defined once,
        imported by every Recharts config.
```

### Type

Geist Sans everywhere (kill the Arial override); Geist Mono for ids, URNs, numbers in
tables. Scale (px/line-height): **12/16** caption · **13.5/20** table+meta · **15/24** body ·
**18/26** section head · **22/30** page title · **34/40** display (Inbox circuit counter).
Weights 400/500/600 only. `font-variant-numeric: tabular-nums` on all metric cells (already
half-adopted; make it a token class).

### Space, radius, depth

4px base grid; page gutter 24px; `max-w-6xl` shell (Explorer's tables earn the width the
current `max-w-5xl` denies them). Radii: 6 (inputs) / 10 (cards) / 14 (dialogs). Depth is
border-first: 1px `--border` everywhere, one shadow token
(`0 4px 16px rgb(0 0 0 / 0.08)`) reserved for overlays.

### Motion

120ms micro (hover, focus, toggles) · 200ms structural (dialog, drawer, toast), both
`cubic-bezier(0.2, 0, 0, 1)`; skeleton pulse 1.6s. Draft generation gets a staged progress
affordance ("writing → rendering visual") driven by the two-call sequence, not a spinner.
`prefers-reduced-motion: reduce` zeroes every duration — a media query in the tokens file,
not per-component.

### States

Every route ships Next.js `loading.tsx` (skeleton mirroring the real layout — no spinners)
and `error.tsx` (plain-words message + retry; the API's `detail` strings are already
human-written, surface them). Empty states are designed, not apologetic: empty Inbox queue
says what would fill it; empty scoreboard states "no generated post has been published yet —
the scoreboard starts with the first circuit". Destructive/irreversible actions (asset
delete, template retire, push to Zernio) get confirm dialogs naming the consequence.

### Accessibility

`:focus-visible` 2px accent ring on every interactive element; AA contrast verified for both
themes (the current `opacity-50`-as-muted-text pattern fails this — replaced by
`--text-muted`); all controls labeled (`aria-label` on the Studio selects, currently bare);
`aria-live="polite"` on busy/generation status; hit targets ≥ 32px; tables get real
`<th scope>`; the AssetPicker grid is arrow-key navigable. Keyboard: `⌘K`-less — this is a
five-page app, tab order and Enter/Escape done right beat a command palette (rejected as
speculative).

### IA after V2

`/` Inbox · `/studio` · `/posts` (+ detail) · `/templates` · `/assets` · `/scoreboard`.
Status page deleted; its health strip renders in the Inbox footer from the same `/health`
response.

---

## What Stays Out — and What Brings It Back

| Item | Trigger to reopen |
|---|---|
| X / Reddit / other channels | ≥10 closed circuits on LinkedIn |
| Comment mining | autonomous mode on and the draft queue running dry |
| Creator watch | proposal review debt at zero + recurring creator material |
| Video pipeline | decision to retire podcast-chopper |
| Optimizer / auto-selection / any ranking | ~300 lineage-tagged posts |
| Per-platform `voice_account` mapping | the channel trigger above (falls out of it) |
| Object storage for media/assets | app runs on more than one machine |
| Comment-to-DM automation | separate product decision (supersedes LinkedIn-Offer-Reward) |
| Multi-user / auth / roles | a second regular user exists |
| Backfilling the ~25 missing scraped posts (2025-08→2026-01) | mostly pre-`voice_since` era; reopen only if extraction quality is ever the suspect |

---

## Needs Nikhil's Real Eyes

Named per the standing blind-AI-trust rule — review these at the granular level:

1. **Anything that touches the live Zernio account.** Push remains draft-only + tagged
   `pixii-intelligence`; V2 adds `asset_values` to the outgoing metadata — read the payload
   diff in `publishing.py` before first push. Test draft `6a691c6d95e614b6077edb22` is still
   in the account awaiting deletion.
2. **Money: image generation.** Every `POST /drafts` with an `ai` visual, every
   regenerate-visual, every template preview is a paid Azure image call, and **there is no
   budget cap or spend counter in the app**. V2 does not add one (usage is single-operator
   and manual); if autonomous mode is ever enabled, insist on a cap first — say so out loud
   at that moment.
3. **Irreversible: asset deletion.** `DELETE /assets/{id}` removes the file from disk. The
   409-if-referenced guard covers drafts; nothing can cover "I still wanted that file."
   Review the guard's test.
4. **Migrations.** All three are additive (one new table, five nullable/defaulted columns).
   Verify no migration in the final diff contains a `drop_*` or column type change.
5. **Verdict notes go to Azure OpenAI.** Lessons are injected into prompts — anything typed
   into a verdict note leaves the machine. Don't put names/numbers there that shouldn't.
6. **The impressions CSV import mutates corpus metrics.** `max()` semantics should make it
   non-destructive; eyeball the before/after on 3 rows the first time it runs.
7. **New frontend dependencies** (Radix primitives, cva/clsx/tailwind-merge): review
   `package.json` diff — the only dependency additions in all of V2.
8. **LinkedIn scraping stays a gray area** run in Nikhil's authenticated tab — unchanged
   from V1, but each re-run is his call, not a scheduled job. It stays out of the scheduler.

---

## Open Questions

1. ~~**Cloudflare Browser Rendering payload ceiling.**~~ **RESOLVED 2026-07-30 by live probe —
   see `.scratch/v2/seams.md`.** No ceiling in the practical range: a **13.17MB** HTML body with
   a 9.88MB incompressible embedded PNG returned a valid 200 PNG, as did two images at 3.51MB.
   Data-URI embedding is sound; **the public-hosting fallback is dropped and the $0 constraint
   holds.** Two corrections fall out of the probe:
   - **The real constraint is request cadence, not bytes.** Free-tier Browser Rendering returns
     `429 code 2001` on sustained calls, and it is indistinguishable from a payload rejection.
     `CloudflareRenderer.screenshot` has no 429 handling today — backoff belongs there, and
     preview-on-keystroke is off the table.
   - **The stated reason to downscale >1600px assets is void.** Resizing still earns its place
     (disk, upload time, a 1080×1350 composite), but no slice may cite a payload limit.
2. **Exact brand orange.** Sample the wordmark hex from a corpus visual in `media/`
   (placeholder `#E8590C` until then).
3. **LinkedIn analytics export columns.** The CSV route assumes post-text + impressions
   columns joinable on content; needs one real export file from Monte to pin the parser.
   The route ships behind that file's arrival — parser is the last slice of Track B.
4. **The 37 pending template proposals.** ~~Recommendation: bulk-retire and re-extract — the
   proposals were derived from the 27-post window and are stale.~~ **Corrected against Postgres
   2026-07-30 — that premise was wrong.** By extraction date:

   | Extracted | Proposed | Corpus then | Verdict |
   |---|---|---|---|
   | 2026-07-28 | 9 (7 STRUCTURE, 2 VISUAL) | 27 posts | stale — retire |
   | 2026-07-29 | 28 (22 HOOK, 6 STRUCTURE; 10 tagged `inspiration`) | 70→107 posts | **fresh — review** |

   So **28 of 37 post-date the widening**, including the whole creator cohort. Bulk-retiring
   discards the re-extraction rather than the stale batch. Revised recommendation: retire the 9
   from 07-28, review the 28 in the Inbox. Still Nikhil's call, one-time.
5. **`enable_autonomous` posture through V2.** Recommendation: stays off until the first
   circuit closes — an unattended queue feeding a gate that has never opened only builds
   backlog. Flag: this is a recommendation, not code; the setting already defaults off.
