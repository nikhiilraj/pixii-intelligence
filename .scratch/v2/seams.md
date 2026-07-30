# V2 — verified seams

Ground truth read from the live tree and live APIs before slicing. A fresh-context slice only
ever sees "tests fail", never "the plan was wrong" — this file is where the plan gets corrected.

Legend: ✅ confirmed as the PRD assumed · ⚠ corrected · 🆕 discovered, not in the PRD

---

## Track A — Asset Library

**⚠ CORRECTED — Cloudflare Browser Rendering payload ceiling (PRD Open Question #1).**
The PRD flagged this as the only technical unknown and warned a failure would force publicly
fetchable assets, reopening the $0 constraint. **Probed live 2026-07-30 — there is no ceiling in
the practical range.**

| embedded PNG (incompressible) | HTML body | result |
|---|---|---|
| 0.67 MB | 0.90 MB | 200, 364KB PNG |
| 2.57 MB | 3.43 MB | 200, 324KB PNG |
| 5.04 MB | 6.72 MB | 200, 330KB PNG |
| 9.88 MB | **13.17 MB** | 200, 297KB PNG |
| 2 × 1.32 MB (logo + product) | 3.51 MB | 200, 378KB PNG |

Data-URI embedding is sound. **No fallback needed; drop the public-hosting contingency.**

**🆕 DISCOVERED — the real constraint is requests/minute, not bytes.** The first probe run read
a `429 {"code":2001,"message":"Rate limit exceeded"}` and it looked exactly like a payload
rejection. Free-tier Browser Rendering rate-limits by request cadence (and daily browser
minutes). Consequences for slicing:
- Any slice that renders in a loop (batch preview, re-render all templates) **must** back off on
  429 and retry, or it will report a size/format bug that isn't one.
- `CloudflareRenderer.screenshot` currently calls `raise_for_status()` with **no 429 handling**
  (`backend/app/rendering.py:94-103`). A retry-with-backoff belongs there, not in callers.
- Template preview-on-every-keystroke is off the table. Preview must be explicit and debounced.

**⚠ CORRECTED — the stated reason to downscale assets.** The PRD justifies resizing >1600px
assets partly because "larger … threatens the render payload limit". That justification is void.
Resize still earns its place (disk, upload time, and the composite is only 1080×1350) — but a
slice must not cite a payload limit that does not exist.

**✅ CONFIRMED, with a trap — `fill()` escapes slot values, and base64 survives it by luck.**
`fill(template_html, values, *, escape=True)` (`backend/app/rendering.py`) runs every slot value
through `html.escape()` on the markup path, because slot values come from a language model. Tested
live 2026-07-30:

```
base64 data URI survives escaping: True
raw SVG data URI survives escaping: False
  → <img src="data:image/svg+xml,&lt;svg xmlns=&quot;http://…&gt;&lt;
```

**Track A needs no change to `fill()`.** Base64's alphabet (`A–Za–z0–9+/=`) plus the prefix
`data:image/png;base64,` contains none of the five characters `html.escape` touches, so the URI
passes through byte-identical. **But that is a coincidence, not a design.** Consequences a slice
must encode:
- **Assets are embedded as base64 data URIs only — never raw or percent-encoded SVG.** A raw
  `data:image/svg+xml,<svg…>` is mangled into `&lt;svg…`, which renders as a broken image with
  **no exception raised** — the exact silent-visual-failure class that retired `stat-hero` v1.
- Do **not** "fix" this by passing `escape=False` on the markup path. That is the XSS/markup-
  injection guard for model-written text; a slice that widens it to serve assets trades a
  cosmetic problem for an injection one.
- Leave a `ponytail:` comment at the embedding site naming base64 as load-bearing, so a later
  SVG-asset feature does not quietly reintroduce this.

**✅ CONFIRMED**
- `WRITABLE_SLOT_TYPES = {"text", "number"}` — `backend/app/generation.py:27`, enforced in
  `writable_slots()` at `:30-35` by `str(slot.get("type") or "") in WRITABLE_SLOT_TYPES`. An
  `image_url` slot is therefore never model-writable, which is the guard Track A must fill from
  the asset library rather than relax.
- `app.mount("/media", StaticFiles(directory=settings.media_dir))` — `backend/app/main.py:46`.
  Assets can be served by the existing mount; no new static plumbing.
- Pillow is already a dependency (`backend/app/rendering.py` imports `Image`) — reading
  width/height and downscaling at upload adds nothing to `pyproject.toml`.
- `CloudflareRenderer` is remote (`base_url="https://api.cloudflare.com"`), so it genuinely
  cannot reach localhost. The data-URI requirement is real, not defensive.
- `lineage_metadata(draft)` — `backend/app/publishing.py:19`, called at `:73`. Adding
  `asset_values` to the outgoing Zernio metadata is a one-function change.
- `render_visual` dispatches on `template.body["renderer"]` (`html` | `ai`) and raises
  `UnsupportedRenderer` rather than falling back silently — asset embedding belongs on the
  `html` path only.

## Track B — Loop Surface

**✅ CONFIRMED — three of the four Inbox queues are already expressible; only one column is
genuinely missing.** `backend/app/models/draft.py` already carries `zernio_post_id` (`:43`) and
`pushed_at` (`:44`), with the comment "Absent means this draft never left the building".

| Inbox queue | Predicate | Exists today? |
|---|---|---|
| Proposals awaiting review | `Template.status == PROPOSED` | ✅ |
| Built, awaiting push | `Draft.pushed_at IS NULL` | ✅ |
| Pushed, awaiting Monte | `pushed_at IS NOT NULL AND published_at IS NULL` | needs `published_at` |
| Published, awaiting verdict | join `Post`, `verdict IS NULL` | needs `verdict` |

The PRD's migration set is therefore correctly minimal — do not add a `DraftStatus` enum. Derive
state from the timestamps that already exist; a status column would duplicate them and they could
then disagree.

`Draft.visual_values` (JSONB, `:37`) holds slot→text. The PRD's separate `asset_values` column is
justified rather than folded in: one dict holding two value *kinds* (prose and asset ids) cannot be
read unambiguously by the renderer.

**✅ CONFIRMED**
- `settings.min_sample_size = 5` — `backend/app/config.py:61`, consumed at `metrics.py:64`
  and surfaced at `main.py:331`. The scoreboard's "insufficient" flagging is real.
- `usable_templates` returns latest-version-and-APPROVED only
  (`backend/app/templates.py`), so retired versions are already invisible to generation.
  This is what makes the PRD's refusal to prune template versions correct.
- The dead `note` parameter is real and already ponytail-flagged as discardable
  (`backend/app/corpus.py:217,231`; `main.py:160`) — safe to delete as the PRD's ledger claims.

**⚠ CORRECTED — the 37 pending proposals are NOT all stale.** The PRD's Open Question #4
recommended bulk-retiring them as products of the 27-post window. Postgres says otherwise:

| extracted | proposed | corpus then | verdict |
|---|---|---|---|
| 2026-07-28 | 9 (7 STRUCTURE, 2 VISUAL) | 27 posts | stale |
| 2026-07-29 | 28 (22 HOOK, 6 STRUCTURE; 10 tagged `inspiration`) | 70→107 posts | **fresh** |

28 of 37 post-date the widening, including the entire creator cohort. Bulk-retire discards the
re-extraction, not the stale batch. Corrected in the PRD.

## Cross-cutting facts every slice needs

**Tests.** The only shared fixture is `session` (`conftest.py:11-27`) — a real Postgres connection in
an outer transaction rolled back per test, all tables wiped first. **There is no shared Zernio
fake**: `FakeZernio` is a per-file class (`test_metrics.py:18-27`) and `test_scoreboard.py` does not
even import it, hand-building rows and overriding `get_session`. A slice told to "use the FakeZernio
fixture" will find none — it must import from `tests.test_metrics` or copy.
**Time is never faked** — no freezegun, no patched clock; tests use real `datetime.now(UTC)` ±
`timedelta`. Publish detection must be assertable without controlling the clock.
**Do not follow `FakeRenderer`** (`test_generation.py:31-38`) — it discards the `html` argument, so it
cannot assert an embedded URI. Follow `renderer_capturing` (`test_rendering.py:33-41`): a real
`CloudflareRenderer` over `httpx.MockTransport` capturing the posted body. Precedent:
`test_slot_values_cannot_break_out_of_the_layout` (`:84-95`).

**Alembic.** Head is **`4a783e8a1648`**. Autogenerate is configured (`env.py` sets
`target_metadata = SQLModel.metadata`) but files are hand-tidied: single quotes,
`server_default=sa.false()` on non-nullable adds, real `downgrade()` bodies. **A new model module not
imported in `app/models/__init__.py` makes autogenerate emit a DROP.**

**Scheduler.** `BackgroundScheduler(timezone="UTC")`; `metrics-sync` every 6h
(`coalesce=True, max_instances=1`), `autonomous-generation` 24h only when `enable_autonomous`.
Started from `main.py:36` lifespan, gated on `enable_scheduler` which **defaults False**
(`config.py:33`). **There are zero scheduler tests** — tests call `sync_metrics(...)` directly, so a
publish-detection test exercises the function and never the job. `sync_metrics` **does not commit**;
callers do (`scheduler.py:29`, `main.py:309`).

**⚠ SAFETY / MONEY — `POST /drafts/autonomous-run` ignores `enable_autonomous` entirely**
(`api_drafts.py:213`). The setting (`config.py:76`, default False) gates **only the scheduled job**,
so the endpoint runs a full autonomous generation pass on demand — which spends Azure image-gen
credits, and there is **no budget cap or spend counter anywhere in the app**. `run_autonomous` never
touches Zernio, so nothing can publish; the exposure is money and surprise, not publication.
Corrected against the PRD's "barely used": autonomous is **fully wired, merely switched off by
default in one of its two entry points.** Flagged for Nikhil; no slice changes it without his say.

**⚠ VISUAL slot data is not uniformly typed.** All five VISUAL rows in Postgres:

| id | name | status | slots |
|---|---|---|---|
| 556 | stat-hero | RETIRED | 4 slots, **no `type` key at all** |
| 557 | lifestyle-scene | PROPOSED | 1 × `text` |
| 798 | product-on-cream | PROPOSED | 1 × `text` |
| 1101 | stat-hero | APPROVED v2 | 2 × `text`, **2 × `image_url`** |
| 1102 | stat-card | APPROVED | 3 × `text` |

Read slot type with `.get("type")`; `slot["type"]` KeyErrors on row 556. `default_asset_id` is a 4th
optional key — JSONB, no migration. The empty-box outcome is **observed** (draft 21, stat-hero v1,
`visual_values.left_image_url = "image CTR"`); `MissingSlotValue` on v2 is proven in-process but
never observed, because **zero drafts have ever used v2.**

## Track C — Design System

**⚠ CORRECTED — Track C's "error states" work has an unnamed prerequisite.** `frontend/src/lib/api.ts`
exists (69 lines) and its only helper is:

```ts
export async function getJson<T>(path: string): Promise<T | null> {
  try { const res = await fetch(...); if (!res.ok) return null; return await res.json() as T }
  catch { return null }
}
```

**Every failure collapses to `null`** — a network error, a 500, a 404 and a legitimately empty
result are indistinguishable at the call site. A slice asked to "add error states" on top of this
can only produce *fake* ones: it would render "nothing here yet" when the API is down. So an
honest-failure API layer is a **prerequisite slice**, not part of the polish pass.

**🆕 DISCOVERED — no mutation helper exists; 7 call sites hand-roll it.** Raw `fetch(` at
`page.tsx:11`, `studio/Studio.tsx:44`, `posts/AddExternal.tsx:25`, `posts/Explorer.tsx:114`,
`posts/[id]/ExcludeToggle.tsx:23`, `templates/TemplateManager.tsx:105` and `:126`. Each carries its
own ad-hoc pending/error `useState`. This is the largest duplication a component layer absorbs, and
it is where a `postJson` belongs.

**🆕 DISCOVERED — `clsx@2.1.1` is already in `pnpm-lock.yaml`** as a transitive dependency. The
component-layer slice adds it as a *direct* dep rather than a genuinely new package; the honest new
additions are the Radix primitives, `class-variance-authority`, `tailwind-merge` and `sonner`.

**Frontend surface a design system must cover — 1,561 lines across 13 files:**

| file | lines | |
|---|---|---|
| `templates/TemplateManager.tsx` | 375 | largest; review queue + extraction controls |
| `posts/Explorer.tsx` | 287 | 4 filter controls → the Select migration target |
| `studio/Studio.tsx` | 210 | generation form; where the asset picker lands |
| `scoreboard/page.tsx` | 122 | read-only, no mutations → safest reskin proof |
| `posts/AddExternal.tsx` | 105 | form → first `postJson` migration |
| `posts/[id]/page.tsx` | 99 | post detail |
| `page.tsx` | 71 | home — what the Inbox replaces |
| `lib/api.ts` | 69 | the wrapper above |
| `posts/[id]/ExcludeToggle.tsx` | 54 | |
| `layout.tsx` | 53 | loads Geist into CSS vars |
| `posts/page.tsx`, `templates/page.tsx`, `studio/page.tsx` | 35/28/27 | thin server shells |
| `globals.css` | 26 | boilerplate |

**✅ CONFIRMED** — the greenfield claim is accurate. `frontend/src/app/globals.css` is 26 lines
of create-next-app boilerplate; `layout.tsx:2` loads `Geist`/`Geist_Mono` into CSS variables and
`globals.css` then overrides `body` with `font-family: Arial, Helvetica, sans-serif`, so the
loaded font is currently unused. Tailwind v4 (`@import "tailwindcss"`, `@theme inline`) — tokens
belong in `@theme`, not a JS config.
