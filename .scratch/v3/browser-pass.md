# Browser pass — the first time this app has ever been looked at

Captured 2026-07-31 against `feat/v3` (identical to `main` @ `7cb37aa`).

**Method.** The Chrome extension was unresponsive again (6th attempt across two sessions), so this
ran through Playwright 1.62.1 driving system Chrome headless against `localhost:3000`:
7 routes × {light, dark} at 1440×900 + light at 390×844, full-page. 21 PNGs in `shots/`.
Layout numbers below are `getBoundingClientRect()` readings, not estimates from the images.

Everything here is something **seen**, not inferred from code.

---

## F1 — BROKEN: every page's `<main>` is shrink-to-fit, so the nav and the page content do not line up

The single most visible defect in the app, present on the home page, and the exact seam US-018
recorded as closed.

`layout.tsx:54` is `<body class="min-h-full flex flex-col">`. Every `<main>` carries
`mx-auto max-w-6xl`. In a flex container, **an auto margin on the cross axis disables `stretch`** —
so `<main>` never fills the line, it falls back to shrink-to-fit, and `mx-auto` then centres
whatever narrow box the content happened to produce. The nav is unaffected because its `max-w-6xl`
div sits inside `<nav>`, an ordinary stretched flex item.

Measured at 1440px, nav content edge = **144px** on every route:

| route | `main.x` | `main.width` | seam vs nav |
|---|---|---|---|
| `/` | 343 | 754 | **+199px** |
| `/posts/95` | 336 | 768 | **+192px** |
| `/posts` | 239 | 962 | **+95px** |
| `/studio` | 144 | 1152 | 0 |
| `/templates` | 144 | 1152 | 0 |
| `/assets` | 144 | 1152 | 0 |
| `/scoreboard` | 144 | 1152 | 0 |

The four zeros are **coincidence, not correctness** — those pages contain something that happens to
reach 1152px. The seam is a function of the data: add a wider row to `/posts` and it shrinks; empty
a queue on `/` and it grows. A layout defect that silently changes size as the database changes is
worse than a constant offset, because it cannot be verified once and trusted.

**Fix:** drop `flex flex-col` from `<body>` (nothing uses it — there is no sticky footer; the
Inbox's `<footer>` is inside `<main>`). `<main>` becomes a normal block, fills the line, and
`mx-auto max-w-6xl` finally means what all eight containers say it means. Alternative if the flex
is wanted later: `w-full` on each `main`, but that is 11 files for what one line fixes.
`items-stretch` does **not** work — auto margins beat it per spec.

Re-measure all seven routes after the change; the whole table must read 144 / 1152.

## F2 — WRONG: the native form controls were never migrated, and they are the loudest thing on two pages

`/studio` renders three raw `<select>` elements (hook, structure, visual). They carry OS chrome —
native arrow, native focus ring, their own border weight and corner radius — sitting directly
beside the tokenised `Button`s, and they are the visual centre of the page.

**Correction (recorded rather than edited away): I originally wrote that `/posts` rendered "four
more" raw selects. It did not.** Its four filter controls were migrated to Radix during V2's
US-004 (`a48bc1c`) and were already correct, sentinel and all, before this run began. What is
genuinely native on `/posts` is only the `dd/mm/yyyy` date input. I inferred the selects from a
screenshot instead of reading the file — the same mistake as the mobile-overflow attribution
below, and from the same cause. The Studio and `/templates` halves were read in source and were
right.

`Studio.tsx:155` already names this: *"the Select migration is US-004's"*. It did not happen. The
asset picker (plan item 2.4) was deferred partly on the grounds that it should move when these
move — so this is now blocking that too.

## F2b — BROKEN: `/templates` is 10384px wide at a 1440px viewport

The worst defect found, and it is on **the page that clears the gate blocking everything else** —
the 37 proposals are reviewed here.

Measured `document.scrollWidth` by route (widest overflowing element named):

| viewport | route | scrollWidth | overflow | widest element |
|---|---|---|---|---|
| 1440 | `/templates` | **10384** | +8944 | `<form class="space-y-3">` |
| 390 | `/templates` | **9880** | +9490 | `<section>` |
| 390 | `/posts` | 784 | +394 | `<main>` |
| 390 | `/scoreboard` | 720 | +330 | `<main>` |

All other route/viewport combinations are exactly viewport-width. Desktop is clean everywhere
except `/templates`.

**Cause** (`TemplateManager.tsx:170`): the layout is `grid gap-10 lg:grid-cols-[1fr_20rem]`. A
`1fr` track carries an implicit `min-width: auto`, which resolves to **min-content** — and the
section's min-content is the longest unwrapped line of `JSON.stringify(t.body, null, 2)` rendered
in the `<pre>` at `:294`. The `overflow-x-auto` already on that `<pre>` never engages, because the
grid track grows to fit before the element is ever asked to scroll. The author column then
stretches to match, which is why the *form* measures widest at 1440.

**Fix:** `min-w-0` on the grid item, or `lg:grid-cols-[minmax(0,1fr)_20rem]`. One class.

**The same shape is latent on `/studio`** (`Studio.tsx:160`, `lg:grid-cols-[22rem_1fr]`). It does
not overflow today only because the draft column's content happens to wrap; a draft containing one
long unbroken string would blow it out identically. Fix both, and fix them the same way.

`/posts` and `/scoreboard` at 390 look like the plain version of the stated rule — a wide table
that should scroll inside its own container. **Retracted below.**

### Retraction: the mobile overflow was F1, not a missing scroll container

I attributed `/posts`'s 784px to the native date input in the filter row, on the strength of the
mobile screenshot showing it at x≈470–610. That was wrong on both counts, and the correction is
worth more than the original claim.

Both tables **already had** their `overflow-x-auto` wrapper (`Explorer.tsx:347`, with a comment
saying exactly why). The wrapper could not do its job because nothing constrained it: while
`<main>` was shrink-to-fit it took `max(min-content, …)` of its contents, so the table's
`min-w-[46rem]` propagated straight through the scroll container into `<main>`'s own width. The
arithmetic is exact and leaves no room for a second explanation:

- `/posts`: 736px (`min-w-[46rem]`) + 48px (`px-6`) = **784** — the measured `main.width`
- `/scoreboard`: 672px (`min-w-2xl`) + 48px = **720** — the measured `main.width`

Instrumenting the scroll containers before and after the one-line `layout.tsx` change:

| route | wrapper client / scroll | scrolls? | fits viewport? | doc.scrollWidth |
|---|---|---|---|---|
| `/posts` before | 736 / 736 | no | no | 784 |
| `/posts` after | 342 / 736 | **yes** | **yes** | **390** |
| `/scoreboard` before | 672 / 672 | no | no | 720 |
| `/scoreboard` after | 342 / 672 | **yes** | **yes** | **390** |

So F1 and the two mobile overflows are **one defect with one fix**, and neither `Explorer.tsx` nor
`scoreboard/page.tsx` needed to change at all. Had I acted on my own reading, the "fix" would have
been a second redundant wrapper around a container that was already correct — and the page would
still have overflowed.

**Method note, and the reason my reading was wrong:** an intermediate probe reported
`recharts-wrapper` as a residual overflower at 736px. That was an artifact of measuring
synchronously inside the same `page.evaluate` that mutated the class — `ResponsiveContainer` resizes
via `ResizeObserver`, which had not fired yet. With a settle delay it reads 390. **Any measurement
taken in the same tick as a layout mutation is suspect**; my date-input reading was probably stale
the same way.

## F3 — ROUGH: both list pages render the entire table with no bound

`/` renders all 37 proposals in one ungrouped column; the page is 2998px tall and the first queue
consumes roughly 60% of it, pushing the three queues that need action furthest down. `/posts`
renders all 69 rows; the page is 4030px tall.

Not broken, and at this corpus size not urgent — but the Inbox is meant to be scanned, and the
queue most people can ignore is the one occupying the fold.

## F4 — WRONG: `/posts` cannot distinguish "measured zero" from "never measured"

Impressions reads `0` and ER reads `0.00` on the large majority of rows. Both are true in the
column sense and false in the reading sense: the 35 scraped rows have **no impressions data at
all**, they are not posts that got zero impressions. The page presents an absence as a measurement.

This is the display half of a known data gap (impressions CSV import, deferred, trigger = Monte's
export). The display half does not need the import to be fixed — an em-dash and a footnote would
do it.

## F5 — ROUGH: `/studio`'s empty state is tinted like a warning

The "No draft yet" card sits on `bg-surface-2`, which renders as a warm cream panel — the same
visual weight the amber failure messages use elsewhere in the app. It is the correct-and-expected
state of the page on every single visit (see plan item 2.1), so it should read as neutral.

## Confirmed working — stated so it is not re-litigated

- The recharts engagement curve on `/posts` renders correctly in light mode, in brand orange.
- The Inbox's four queues, their counts, ages, and the "nothing waiting on a verdict" empty state
  all render exactly as designed.
- The system-status footer is legible and correct (all six green).
- Nav is present, correctly ordered, and identical on every route.

---

## F6 — WRONG: dates omit the year on a corpus spanning 2.5 years

`Explorer.tsx:77` formats every date as `{day: "2-digit", month: "short"}` — no year. The corpus
runs **2024-01-25 → 2026-07-17**, so the engagement chart's axis reads
`26 Jan · 27 Feb · 14 Mar · 17 Sept · 01 Apr · …`, which looks like the series is out of order.

**It is not.** `series` at `:200` sorts ascending by `published_at` before mapping; the ordering is
correct and the label is what lies. Recorded because the first reviewer to see that axis will
conclude the chart is broken and go looking for a sorting bug that does not exist.

The same formatter feeds the table's `Published` column, where it is worse: the table is sorted by
engagement, so a 2024 row reading "15 Jun" sits directly above a 2026 row also reading "15 Jun".

## F7 — WRONG: the three scoreboard tables do not share a column grid

`Status` lands at x=646 under HOOK, x=666 under STRUCTURE and x=504 under VISUAL — a ~160px jump,
with Posts / Engaged / Mean / Evidence shifting behind it. Three tables of the same shape, stacked,
that read as unrelated. Each sizes its own columns to its own content.

## F8 — ROUGH: dark mode is genuinely fine, and the suspicion about it was wrong

Worth stating plainly so it stops being carried forward: `/posts/[id]` — the known un-tokenised
holdout — shows **no** light-mode leakage in dark mode. No white panels, no black-on-dark text;
panels, borders, inputs and the textarea are all correctly dark. The large cream block mid-page is
the post's own artwork, not a leak. The recharts orange reads clearly against the dark plot area.

The pre-token holdouts are a consistency problem, not a legibility one. That downgrades them.

## F9 — ROUGH: assorted, recorded and not promoted

- `/assets` dropzone renders an unstyled native `Choose file / No file chosen` control.
- `/studio`'s two buttons are near-identical muted chips — no primary action, and both read as
  disabled even when enabled.
- `/posts` ER column sits flush against the row rule with no right padding.
- `/assets` cards with two-line titles push their metadata rows out of alignment with neighbours.

---

## Promoted to `prd.json`

| finding | promoted | why |
|---|---|---|
| **F2b** `/templates` 10384px overflow | **yes — first** | Approve/retire sit ~8500px off-screen. The 37-proposal gate that blocks everything is cleared on this page. |
| **F1** nav/content seam, shrink-to-fit `main` | **yes** | Home page, every route, and it moves with the data. One line. |
| **F2** native form controls never migrated | **yes** | Loudest inconsistency in the app, and it blocks plan item 2.4. |
| **F6** year missing from dates | **yes** | Cheapest of the set and it makes a correct chart look broken. |
| **F7** three scoreboard column grids | yes | Contained; one table component. |
| F3 unbounded lists | no | Not urgent at 37/69 rows. |
| F4 zero-vs-unmeasured | no | Pairs with the impressions import; same trigger. |
| F5, F9 | no | One token each; batch them into whichever slice touches the file. |

**The F1 fix is proven, not assumed.** Removing `flex flex-col` from `<body>` and setting
`min-width: 0` on the grid children was applied at runtime in the browser and all seven routes
re-measured:

| route | before `x/w` | after `x/w` | scrollWidth |
|---|---|---|---|
| `/` | 343 / 754 | **144 / 1152** | 1440 → 1440 |
| `/posts` | 239 / 962 | **144 / 1152** | 1440 → 1440 |
| `/templates` | 144 / 1152 | 144 / 1152 | **10384 → 1440** |
| `/studio`, `/assets`, `/scoreboard` | 144 / 1152 | 144 / 1152 | 1440 → 1440 |
| `/posts/95` | 336 / 768 | 336 / 768 | 1440 → 1440 |

`/posts/95` is unchanged **and that is correct** — it is deliberately `max-w-3xl`, a reading
measure for a single post. It is centred, so it reads as intentional rather than as the F1 seam.

The rest of the deferred cosmetic list (recharts onto tokens, `Card` duplication, `/posts/[id]`
tokenisation, `TemplateManager.tsx:291`) stays deferred, and F8 is the reason: the pass found no
legibility or dark-mode failure in any of them. They are consistency debt, not defects.

---

# US-021 — the second browser pass, after 20 slices

Captured 2026-08-04 against `feat/v3` @ `b00e6a7` (40 commits, tree clean, `make check` green at
460 backend + 233 frontend before this pass began).

**Method.** Chrome extension not attempted (7th attempt would have been). Playwright 1.62.1 driving
system Chrome against `localhost:3000`, **7 routes × 2 themes × 2 viewports = 28 combinations**,
full-page *and* viewport-clipped PNG for each (56 images in `shots/us021/`). Every number below is
a `getBoundingClientRect()` / `getComputedStyle()` reading taken after two `requestAnimationFrame`s
plus a 700ms settle, never in the same tick as a mutation. Colour numbers are resolved through a
1×1 canvas — Tailwind 4 emits `lab()`/`oklch()` and a regex over `getComputedStyle().color` parses
the lab components as RGB and lies (that mistake was made and caught inside this pass).

**What "looked at" means here, precisely:** the viewport-clipped shot of **all 28** route × theme ×
viewport cells was opened and read as an image, plus the full-page shot of
`scoreboard-light-desktop` and `postdetail-light-desktop`, plus all eight shots of the two
never-rendered surfaces. The remaining full-page images (`/templates` at 22362px downscales to
something no one can judge) were used for structure via measurement, not by eye — said out loud so
this does not read as a claim that 56 images were each studied. Findings below are things **seen or
measured**, never inferred from source.

## Verdict: **STOP — do not merge.** Two WRONG findings.

## The measurement table — 28 of 28 layout invariants hold

`main.x` / `main.width`, `document.scrollWidth`, page height, `<body>` background, and
`table` counts. Light and dark are byte-identical on every layout number at both viewports, which
is itself evidence the theme switch changes only colour.

| route | theme | vp | main.x | main.w | scrollWidth | docHeight | body bg | tables all/main |
|---|---|---|---|---|---|---|---|---|
| `/` | light | 1440 | **144** | **1152** | 1440 | 1703 | `rgb(250,250,247)` | 0/0 |
| `/studio` | light | 1440 | **144** | **1152** | 1440 | 974 | `rgb(250,250,247)` | 0/0 |
| `/posts` | light | 1440 | **144** | **1152** | 1440 | 1912 | `rgb(250,250,247)` | 1/1 |
| `/posts/95` | light | 1440 | **336** | **768** | 1440 | 2668 | `rgb(250,250,247)` | 0/0 |
| `/templates` | light | 1440 | **144** | **1152** | 1440 | 19615 | `rgb(250,250,247)` | 0/0 |
| `/assets` | light | 1440 | **144** | **1152** | 1440 | 900 | `rgb(250,250,247)` | 0/0 |
| `/scoreboard` | light | 1440 | **144** | **1152** | 1440 | 3666 | `rgb(250,250,247)` | **15/3** |
| `/` | light | 390 | 0 | 390 | **390** | 2331 | `rgb(250,250,247)` | 0/0 |
| `/studio` | light | 390 | 0 | 390 | **390** | 1340 | `rgb(250,250,247)` | 0/0 |
| `/posts` | light | 390 | 0 | 390 | **390** | 2620 | `rgb(250,250,247)` | 1/1 |
| `/posts/95` | light | 390 | 0 | 390 | **390** | 2617 | `rgb(250,250,247)` | 0/0 |
| `/templates` | light | 390 | 0 | 390 | **390** | 22362 | `rgb(250,250,247)` | 0/0 |
| `/assets` | light | 390 | 0 | 390 | **390** | 1204 | `rgb(250,250,247)` | 0/0 |
| `/scoreboard` | light | 390 | 0 | 390 | **390** | 3858 | `rgb(250,250,247)` | **15/3** |
| `/` | dark | 1440 | **144** | **1152** | 1440 | 1703 | `rgb(18,18,16)` | 0/0 |
| `/studio` | dark | 1440 | **144** | **1152** | 1440 | 974 | `rgb(18,18,16)` | 0/0 |
| `/posts` | dark | 1440 | **144** | **1152** | 1440 | 1912 | `rgb(18,18,16)` | 1/1 |
| `/posts/95` | dark | 1440 | **336** | **768** | 1440 | 2668 | `rgb(18,18,16)` | 0/0 |
| `/templates` | dark | 1440 | **144** | **1152** | 1440 | 19615 | `rgb(18,18,16)` | 0/0 |
| `/assets` | dark | 1440 | **144** | **1152** | 1440 | 900 | `rgb(18,18,16)` | 0/0 |
| `/scoreboard` | dark | 1440 | **144** | **1152** | 1440 | 3666 | `rgb(18,18,16)` | **15/3** |
| `/` | dark | 390 | 0 | 390 | **390** | 2331 | `rgb(18,18,16)` | 0/0 |
| `/studio` | dark | 390 | 0 | 390 | **390** | 1340 | `rgb(18,18,16)` | 0/0 |
| `/posts` | dark | 390 | 0 | 390 | **390** | 2620 | `rgb(18,18,16)` | 1/1 |
| `/posts/95` | dark | 390 | 0 | 390 | **390** | 2617 | `rgb(18,18,16)` | 0/0 |
| `/templates` | dark | 390 | 0 | 390 | **390** | 22362 | `rgb(18,18,16)` | 0/0 |
| `/assets` | dark | 390 | 0 | 390 | **390** | 1204 | `rgb(18,18,16)` | 0/0 |
| `/scoreboard` | dark | 390 | 0 | 390 | **390** | 3858 | `rgb(18,18,16)` | **15/3** |

- The six shell routes read exactly `144 / 1152` at 1440 in **both** themes. US-001's fix holds.
- `/posts/95` holds at `336 / 768` — the deliberate `max-w-3xl` reading measure. Not widened.
- `document.scrollWidth` equals the viewport on **all 28**. Nothing overflows the document.
- `/scoreboard` returns 15 `table` elements under `<body>` and **3** under `main` — Next's twelve
  hidden streaming artifacts, exactly as briefed. Scoped to `main table`.
- The two 390px table overflows are *contained*, not clipped, and I checked rather than assumed:
  `/posts` wrapper `overflow-x: auto`, client **342** / scroll **736**; `/scoreboard` × 3, client
  **342** / scroll **752**. They scroll inside their own container, which is the stated rule.
- All 61 `<pre>` blocks on `/templates` are `overflow-x: auto`, `white-space: pre`, widest
  `scrollWidth` **9830** inside a **718**px box, and **zero** are `overflow-x: hidden`. The
  10384px defect stays fixed and the content stays reachable.

## Console — clean on all 28

`console` (error + warning), `pageerror` and `requestfailed` listeners attached **before** each
`goto`, buffer segmented per route, and every route reached by a full `goto` rather than by
clicking nav links — a client-side router transition does not re-run hydration and would have
hidden a mismatch. **Zero messages of any kind, on every one of the 28 combinations.** No
hydration mismatch survives in this build.

## W1 — WRONG: the date filter's calendar button is invisible in dark mode

`/posts` renders the app's only visible native control, `<input type="date">` (the one holdout
US-003 left). `color-scheme` is **never declared anywhere in the stylesheet** —
`getComputedStyle(document.documentElement).colorScheme` reads `normal` in both themes — so
Chrome paints the UA shadow-DOM `::-webkit-calendar-picker-indicator` with light-mode chrome
regardless of the page. The *text* is fine (it inherits `--text`); the glyph is not.

Measured from the rendered pixels (`shots/us021/dateinput-{light,dark}.png`, 3× DPR):

| theme | glyph | field | contrast | floor (non-text UI) |
|---|---|---|---|---|
| light | `rgb(0,0,0)` | `rgb(250,250,247)` | **20.08:1** | 3:1 |
| dark | `rgb(0,0,0)` | `rgb(18,18,16)` | **1.12:1** | 3:1 |

A black icon on a `#121210` field. The affordance that opens the date picker is not merely low
contrast, it is not there. Half the app's users see a text field with no picker.

**Proven fix, applied at runtime in Chrome and re-measured** —
`document.documentElement.style.colorScheme = "light dark"`:

| theme | glyph | field | contrast |
|---|---|---|---|
| dark, with `color-scheme` | `rgb(18,18,16)` | `rgb(255,255,255)` | **18.75:1** |
| light, with `color-scheme` | `rgb(0,0,0)` | `rgb(250,250,247)` | **20.08:1** — unchanged |

One declaration in `globals.css`. `shots/us021/dateinput-dark-colorscheme.png` is the after.

`color-scheme: normal` is a *class* of latency, not one icon: it also governs UA scrollbars (macOS
overlay scrollbars hide this locally; a classic-scrollbar platform gets a white scrollbar on the
dark `<pre>` and table scrollers), autofill, and any native control added later. The other native
controls were checked and are not affected — the file inputs on `/assets` and in the Studio picker
are visually hidden (`clip-path: inset(50%)`, width 1px) behind styled labels, and the textarea
resize grip measures 1.50:1 dark vs 1.33:1 light, i.e. equally faint in both and not a dark-mode
regression.

## W2 — WRONG: the three routes that were never tokenised mute text below AA, and this **retracts F8**

The first browser pass closed F8 with this, and it is the claim being withdrawn:

> The pre-token holdouts are a consistency problem, not a legibility one. That downgrades them.

They are a legibility problem. F8 was right that dark mode has no *leak* — no white panel, no
black-on-dark text — and that finding survives. What it did not do was compute a contrast ratio,
so "no legibility failure" was a look, not a measurement. Measured now, `/studio`'s left column,
`/posts/[id]` and `/templates` fail AA in **light** mode, and one element fails in both.

### W2a — `opacity-*` used as a muting token instead of `--text-muted`

`--text` at `opacity-50` over `--bg` composites to `rgb(137,137,135)` on `rgb(250,250,247)` =
**3.33:1** at 12px, against a 4.5 floor. `opacity-40` is worse. Measured on the rendered page at
both viewports, with ancestor opacity accumulated down the chain and applied to *both* the text
and the backdrop it is composited over:

| element | file | opacity | light | dark | floor |
|---|---|---|---|---|---|
| `cohort unrecorded` | `TemplateManager.tsx:100` | 0.4 | **2.51:1** | **3.40:1** | 4.5 |
| `Drafts` / `Lineage` headers | `Studio.tsx:415`, `:470`, `:747` | 0.5 | **3.33:1** | 4.64:1 | 4.5 |
| `ENGAGED ACTIONS` … metric captions, `Generated from`, `Engagement over time`, `v{n}`, `not recorded` | `posts/[id]/page.tsx:26`, `:61`, `:65`, `:68`, `:108`, `:230`, `:245` | 0.5 | **3.33:1** | 4.64:1 | 4.5 |
| `Proposes patterns from…`, `{kind} · v{n}`, and the author-panel headers | `TemplateManager.tsx:239`, `:267`, `:312`, `:334` | 0.5 | **3.33:1** | 4.64:1 | 4.5 |
| body copy at `opacity-60` (**19** sites, counted) | `studio/page.tsx`, `Studio.tsx` ×5, `AddExternal.tsx`, `ExcludeToggle.tsx`, `RetopicForm.tsx` ×4, `posts/page.tsx`, `posts/[id]/page.tsx` ×6 | 0.6 | 4.55:1 | pass | 4.5 |

`cohort unrecorded` is the only element in the app that fails in **both** themes, and it fails
worst — 2.51:1 is a little over half the required contrast, on the label that tells a reviewer
whether a proposal came from our own posts or was borrowed. It is visible as a defect in
`shots/us021/templates-light-mobile-fold.png` without any instrument.

Counted, not estimated: `/templates` renders **107** nodes at `opacity-50` and **38** at
`opacity-40`; `/posts/95` renders **6** at `opacity-50`; `/studio` **1**. The rows above are
deduplicated by resolved colour pair, so one row is one colour, not one element.

The `opacity-60` row is worth naming precisely because it *passes*: 4.55:1 against a 4.5 floor is
1% of headroom, which is not a margin, it is a coincidence. Dark `opacity-50` at 4.64:1 is the
same. The tokenised path (`--text-muted`, used on `/`, `/posts` and `/assets`, which came back
clean on every route and both themes) is what removes the coincidence.

This is the sweep US-019 never ran. It removed exactly one `opacity-50` — the Studio empty state —
and recorded *"`opacity-50` … which the PRD names as an AA failure — is now --text-muted"*. The
other 152 instances were not looked for.

**Method note, and it cost a wrong claim inside this pass:** the first version of the sweep skipped
only `opacity === "0"` and never multiplied fractional opacity into either layer, so it reported
`/studio` and `/posts/95` as clean and I wrote that down. Every element carrying an `opacity-*`
class was invisible to it. A contrast probe that does not accumulate ancestor opacity is measuring
a page that is not on screen.

### W2b — `/templates` has a private Badge that does what the shared Badge exists to prevent

`TemplateManager.tsx:77-84` declares its own `Badge` over a local `STATUS_STYLE` map built from raw
Tailwind palette colours — `text-amber-700`, `text-emerald-700`, and `opacity-60` — instead of
`components/ui/badge.tsx`. That shared component's header comment is a measurement and a rule:

> The status colours are FILLS, not text colours — measured, and this contradicts the PRD. […] a
> Badge tints the status colour behind a `--text` label instead of colouring the label.

The private copy colours the label. Measured on the rendered page at 12px, alpha-composited
through every ancestor, `opacity` applied where present:

| badge | theme | text | fill | contrast | AA floor |
|---|---|---|---|---|---|
| `proposed` | light | `rgb(187,77,0)` | `rgb(250,235,210)` | **4.31:1** | 4.5 |
| `approved` | light | `rgb(0,122,85)` | `rgb(212,240,229)` | **4.47:1** | 4.5 |
| `retired` | light | `rgb(108,108,106)` | `rgb(234,234,231)` | **4.34:1** | 4.5 |
| `proposed` / `approved` / `retired` | dark | — | — | pass | 4.5 |

All three light-mode states are under AA; dark mode passes. Individually each is a near miss
(0.03–0.19 short). Together they are the same defect three times, on the page that clears the
50-proposal gate, and the fix is to delete the local component and use the tokenised one — which
already carries the measurements that say why.

The `retired` style also uses `opacity-60`, so it is W2a and W2b at once.

**Scope of the sweep, stated so it is not over-read:** `main *` only, so `layout.tsx`'s
`opacity-70` nav links were never in it — computed separately, they clear at ~6.3:1 light and
~8:1 dark. Within that scope, and with ancestor opacity applied, **every text node that is not
listed in W2a or W2b clears AA in both themes at both viewports.** `/`, `/posts`, `/assets` and
`/scoreboard` are clean end to end — those are the routes that use `--text-muted`. Sweep is
`scratchpad/us021-contrast2.cjs`.

## The two surfaces that had never been seen rendered — both correct

Served from a **read-only proxy** on `:8100` with an isolated frontend on `:3100`
(`scratchpad/us021-stub-api.js`, `scratchpad/fe-stub` re-synced from current `frontend/src`;
`package.json` and `pnpm-lock.yaml` verified byte-identical to the real app first). The proxy is
GET-only *by construction* — it calls `fetch(UPSTREAM + req.url)` with no method, headers or body,
so a POST from the browser is replayed upstream as a GET and cannot write. Exactly two fields are
patched on the way out.

**Verdict retract control** — patched `post 95` to carry `verdict: "worked"` and a note. Rendered
in all four theme × viewport combinations: `Recorded: Worked` badge, `Worked` as the pressed
primary, the note in the textarea at `115 of 500 characters`, the button relabelled
`Change the ruling`, then a rule, the "retract if you should not have ruled at all" copy, and
`Retract the ruling`. Arming it (React state only, no request) swaps in `Yes, retract it` /
`Keep the ruling`. `document.scrollWidth` 1440 / 390. Zero console errors.
`shots/us021/surface-retract-{light,dark}-{desktop,mobile}.png` and `-armed-`.

**"Use the template's default"** — patched template 1101 (`stat-hero` v2, approved visual) so its
`left_image_url` slot names `default_asset_id: 522`, an asset that really exists. Choosing that
visual in Studio pre-fills the slot from the template's defaults and opens with the dialog reading
*"Picking nothing leaves this slot on the template's own default, "Readiness index"."*; the
Readiness index tile carries `aria-pressed="true"`, the accent border and the trailing
`— template default`; the button renders beneath the grid. Dialog 672×342 at 1440, 342px at 390
with the page still at `scrollWidth` 390. All four combinations. Zero console errors.
`shots/us021/surface-tpldefault-{light,dark}-{desktop,mobile}.png`.

**The database is unchanged**, and this was checked rather than asserted — counts and `max(id)`
snapshotted before any browser work and diffed after everything above:

```
asset|2|522   draft|6|5493   metric_snapshot|100|164   post|107|6895   template|63|62646
verdicts|0    APPROVED|11    PROPOSED|50    RETIRED|2
```

`diff` of before/after is empty — and it was re-taken **after the last browser session of the
whole pass**, not just after the proxy work, so the evidence covers every navigation made here.
The proxy log records zero non-GET upstream calls, because it cannot make one. On `:3000` nothing
was ever clicked: `goto` + `evaluate` only, which is why the 13 template rows and the junk draft
of the previous run have no counterpart in this one.

## Decision 1 — the global long-unbroken-string overflow: **recorded, not changed**

The ledger's evidence is a synthetic 300-character token. The question is whether real data
reaches it, so I measured the corpus instead of reading classnames.

Longest whitespace-free run in every field `<main>` renders as prose, over the live API:

| field | longest run | where |
|---|---|---|
| `post.content` | 122 | post 85 — `https://www.pixii.ai/frameworks/episodes/super-cool-ditching-…` |
| `template.body` JSON line | 1363 | template 1101 (inside a `<pre>`) |
| `template.name` | 35 | `simple-diagnostic-engagement-lesson` |
| `draft.full_text` | 16 | `straightforward:` |
| `asset.label` | 10 | `comparison` |

The 122-character URL is not a 122-character *unbreakable* run: UAX-14 gives Chrome a break
opportunity after every `/` and `-`, and that URL is made of them. Re-measuring with those
excluded, the longest genuinely unbreakable run **in the entire corpus is 22 characters** — the
hashtag `#accountbasedmarketing` — plus one 68-character rule of literal dots in post 6893.

Measured, not inferred: `/posts/85`, `/posts/6893`, `/posts/2996`, `/posts/6894`, `/posts/6884`,
`/posts/89` and `/posts/95` all render at `document.scrollWidth == 390` inside a 342px measure,
and so does every one of the 28 combinations in the table above.

Against that, a global `wrap-anywhere` on `<main>` is a large change: `overflow-wrap: anywhere`
is precisely the value that **contributes soft-wrap opportunities to min-content sizing** (that
property is why `Studio.tsx:540` says `break-words` would not do), so applying it at the shell
would change the min-content of every table cell and grid track in the app — including
`/posts`'s `min-w-[46rem]`, `/scoreboard`'s three `table-fixed` tables, and the `[1fr_20rem]` and
`[22rem_1fr]` grids that two slices were spent getting right. Seven routes of blast radius to fix
a symptom no real row produces.

The surfaces that *can* receive arbitrary human or model text already carry the local fix, applied
deliberately and with measurements: `Studio.tsx:304` and `:557`, `AssetLibrary.tsx:368`/`:375`,
`posts/[id]/page.tsx:64`. That is the correct shape — narrow, at the entry points, where the
min-content consequence is intended.

**Verdict: leave the shell alone.** Recorded here so the next reader does not re-derive it. The
trigger to revisit is real data crossing roughly 40 unbreakable characters — a base64 blob or a
tracking URL without separators arriving in `post.content` or `draft.full_text`.

## Decision 2 — `Studio.tsx:525`'s variants summary card keeps `bg-surface-2`

US-019's argument for stripping the tint off the empty state is in its own comment, and it is
specific: *"it sat here as a tint on the one state that is correct on every visit to a fresh
Studio, beside a column whose other two states are a real failure (the `missing` alert) and a real
warning."* The premise is **default correctness**. It does not transfer.

The variants summary is not a default state. It exists only after someone pressed Write variants
and spent money, and its text is the report of that spend plus a genuine caution — *"This batch
spent 3 chat completions and 3 image renders. Keeping one **deletes** the rest."* A raised panel
is what that is.

`bg-surface-2` is also not a warning vocabulary in this app; it is the standard raised-card fill,
and I checked every use rather than reasoning from one. It carries the Inbox's `Closed circuits: 0`
panel (`(inbox)/page.tsx:163`), the Inbox's queue empty states (`:123`), the Scoreboard's
"No generated post has been published yet" banner (`scoreboard/page.tsx:98`, `:112`),
`TemplateManager.tsx:250`, `Explorer.tsx:494`, and the `neutral` variant of the shared Badge. I
looked at four of those rendered in both themes in this pass; none reads as a warning. The
Studio empty state was a local contrast problem inside one column, not a claim about the token.

**Verdict: no change.** Stripping it would also flatten the summary into the three variant cards it
is summarising, which are plain bordered boxes.

## Recorded, not promoted — COSMETIC

- **`/templates` is 19615px tall at 1440 and 22362px at 390.** 61 cards, every template, no filter
  by kind or status, no bound — `initial.map` over the whole library. US-018 bounded `/` (6 of 50,
  with a caption) and `/posts` (paged), and deliberately did not bound this one: the Inbox's own
  caption calls it *"the page that clears the rest"*, so a bound here would defeat its purpose.
  Everything on it renders correctly and is reachable. It is a missing affordance (filter or group
  by status/kind), not a rendering defect. Naming it because it is now the longest page in the app
  by 5×, and clearing 50 proposals means scrolling ~13000px.
- **`/scoreboard` lists all 61 template versions**, including the 50 `proposed` ones that
  structurally cannot have data — only an approved template can be generated from. Every row reads
  `0 · 0 · — · too thin (0/5)`. The page's own banner explains why every number is zero, so this is
  honest rather than misleading; it is 50 rows of guaranteed-empty.
- **`/posts` ER column** still sits ~9px from the table's right edge (prior F9). Unchanged.
- **The Studio asset picker's upload label truncates to `Choose an …`** at 390 in the dialog.
- **"Use the template's default" is a no-op in the state it first appears in** — the picker opens
  pre-filled *with* the template default, so the button offers to swap an explicit pick of 522 for
  an implicit one and nothing visibly changes. Semantically real (the backend re-resolves the
  default at render time) and the dialog description explains it; only the first-open case looks
  redundant.
- **`Yes, retract it` renders with a `--surface-2` fill in the armed screenshots** while
  `Keep the ruling` does not. Both are `variant="outline"`. This is `hover:bg-surface-2` under the
  automation's cursor, which is parked where the trigger used to be — an artifact of the capture,
  not a defect. Stated so the screenshot is not misread.

## Confirmed fixed by this pass — stated so it is not re-litigated

- **F1 / F2b** — the shell holds at `144/1152` on six routes and `336/768` on `/posts/95`, in both
  themes, and `/templates` is 1440 not 10384. Re-measured, not inherited.
- **F2** — zero raw `<select>` anywhere in `main` on any route. The date input is the only native
  control left, and W1 is about it.
- **F4** — `/posts` prints `—` for unmeasured impressions and ER, not `0` / `0.00`. Seen.
- **F5** — the Studio empty state is a plain `Card`. Seen in both themes.
- **F6** — every date carries its year: `15 Jun 26`, `25 Jan 24`, `28 Feb 24`, and the chart axis
  reads `08 Feb 24 → 28 Jul 26` in order.
- **F7** — the three scoreboard tables share one column grid; `Status`, `Posts`, `Engaged`, `Mean`
  and `Evidence` land on the same x under HOOK, STRUCTURE and VISUAL.
- **F8, in half** — dark mode holds after nine more slices *as a leak question*: every route seen
  in dark, no light-mode leak, no black-on-dark text, recharts orange reads against the dark plot
  area. **F8's second sentence is retracted** — see W2. "The pre-token holdouts are a consistency
  problem, not a legibility one" was a look, not a ratio, and the ratios say otherwise in light
  mode. The one *dark*-mode failure found here, W1, is a UA-painted glyph the stylesheet never
  claimed.
- **F9's dropzone** — `/assets` renders a styled "Drop an image here, or choose a file" panel; the
  native `Choose file / No file chosen` control is gone.

## What this pass says about the gate

**Neither W1 nor W2 was introduced by V3.** W1 is the `<input type="date">` US-003 knowingly left
native after migrating every `<select>` — it was never inspected in dark mode, because
`color-scheme` is a property nobody thinks to look for until they see a missing icon. W2 is the
set of pre-token holdouts the *first* browser pass explicitly deferred, on the record, as
"consistency debt, not defects." Both are older than this branch; both survived because the
question that would have caught them was never asked.

Twenty slices, two adversarial review passes and 693 green tests did not surface either. Both are
colour. One is drawn by the browser in a shadow root no test can query; the other was invisible to
my own first sweep because that sweep did not accumulate `opacity`. A green suite is not evidence
about a rendered page — and neither is a probe that measures the wrong page.

Per US-021's rule: **anything WRONG stops the merge.** `passes` stays `false` in `prd.json`,
nothing is merged, and this file is written but **not committed** — git was scoped to Part 3 and
Part 3 did not trigger. The tree is dirty by design; the two findings go back to the human who
owns the call.

---

# US-021, part 2 — the two AA failures fixed, re-measured, and merged

Captured 2026-08-04 against `feat/v3` @ `b00e6a7` + this change. The session that wrote everything
above stopped at the gate rather than merging, which was the right call and is why these numbers
exist. This section closes W1 and W2 and records what was deliberately left alone.

**Instrument.** The same sweep, unchanged — `scratchpad/us021-contrast2.cjs` — run **before** the
edits to confirm it still reproduces the documented failures (it did, all five light-mode rows and
the one dark row, to the second decimal), then after. Plus two additions:
`us021b-ratios.cjs`, which reports **every** distinct colour pair with its ratio rather than only
the ones under the floor, so a passing element can be compared before and after; and
`us021b-named.cjs`, which measures named elements. All three resolve colour through a 1×1 canvas
and multiply accumulated ancestor `opacity` into both layers, and all measure after two `rAF`s and
a 700ms settle.

**The "before" column is measured, not remembered.** The pre-fix numbers for elements that already
*passed* — dark-mode `opacity-60`, the dark badges — were never printed by a sweep that only
reported failures. Rather than compute them, `frontend/src` was stashed, the dev server allowed to
rebuild, the named probe re-run, and the stash popped. Both columns therefore come from the same
instrument on the same running app.

## W1 — CLOSED. `color-scheme: light dark` in `:root`

`frontend/src/app/globals.css`, one declaration beside the token block, with the reason in a
comment naming the native picker chrome, so it is not deleted as decoration.

Measured from rendered pixels at 3× DPR — crop of the picker indicator, modal colour as the field
and the pixel furthest from it in luminance as the ink. The arms are the shipped page and the same
page with `color-scheme: normal` re-injected at runtime, which *is* the pre-fix state, so both
numbers come from one crop and one definition:

| control | theme | before | after | floor |
|---|---|---|---|---|
| `::-webkit-calendar-picker-indicator` | dark | `rgb(0,0,0)` on `rgb(18,18,16)` — **1.12:1** | `rgb(255,255,255)` on `rgb(18,18,16)` — **18.75:1** | 3 |
| `::-webkit-calendar-picker-indicator` | light | `rgb(0,0,0)` on `rgb(250,250,247)` — 20.08:1 | **20.08:1 — unchanged** | 3 |

Exactly the runtime proof recorded above, now in the stylesheet.
`shots/us021/dateinput-{light,dark}-fixed.png`, and the glyph is visible in context in
`shots/us021/posts-dark-desktop-fold-fixed.png`, not only in the crop.
`getComputedStyle(documentElement).colorScheme` reads `light dark` on all 28 combinations.

**The two adjacent controls were re-confirmed, not assumed** — and one of them corrects a number
recorded above:

- **File inputs** — still `clip-path: inset(50%)`, 1×1, `position: absolute`, in both themes, on
  `/assets` and in the Studio picker. Visually hidden behind styled labels, unaffected.
- **Textarea resize grip** — **5.67:1 light / 7.17:1 dark, and byte-identical in both arms of the
  A/B.** `color-scheme` does not govern it in this Chrome, so it is neither fixed nor broken by
  this change. That contradicts the **1.50 / 1.33** pair recorded above, which the same A/B does
  not reproduce; it was a method artifact (the earlier reading cannot have been the ink, since
  light-mode UA painting is provably unchanged here — the light glyph reads 20.08:1 in both arms).
  The *conclusion* it supported survives and is now measured rather than looked at: the grip is
  symmetric across themes and is not a dark-mode regression. `shots/us021/textarea-grip-*-fixed.png`
  show the diagonal in both.

## W2 — CLOSED. 38 opacity sites onto `--text-muted`, and the private Badge deleted

**W2a.** Every `opacity-40` and `opacity-50` text site in `main`, plus all 22 `opacity-60` sites,
now carry the `text-muted` utility. The `opacity-60` group was converted rather than recorded:
4.55:1 against a 4.5 floor is 1% of headroom, which the brief's own threshold (~4.8) puts inside
the fix, and the subtrees were text-only so the swap was mechanical. Counted from the diff:
**1 × `opacity-40`, 15 × `opacity-50`, 22 × `opacity-60`, across 8 files.**

`opacity` fades a whole subtree including its fills; `text-muted` sets a colour. Every site was
read before it was changed, and all 38 are text-only leaves — the one place opacity was doing real
work on a fill was `retired`'s `bg-black/10 opacity-60`, which the Badge deletion supersedes.

| element | file | light before → after | dark before → after |
|---|---|---|---|
| `cohort unrecorded` | `TemplateManager.tsx` (CohortTag) | **2.51 → 5.64** | **3.40 → 6.25** |
| `Proposes patterns…` | `TemplateManager.tsx:253` | **3.33 → 5.64** | 4.64 → 6.25 |
| `{kind} · v{n}`, `from N posts`, `Author a template` | `TemplateManager.tsx:281`, `:326`, `:348` | **3.33 → 5.64** | 4.64 → 6.25 |
| `Drafts`, `Lineage`, `Images — n slots` | `Studio.tsx:415`, `:470`, `:747` | **3.33 → 5.64** | 4.64 → 6.25 |
| metric captions | `posts/[id]/page.tsx:26` | **3.33 → 5.64** | 4.64 → 6.25 |
| body copy at `opacity-60` | 8 files | 4.55 → 5.64 | 6.19 → 6.25 |

Rows are deduplicated by resolved colour pair, as above: one row is one colour, not one element.

**Which of the 38 were actually on screen, stated because this document's standard is "seen or
measured, never inferred from source" and 16 of them fail that standard.**

Rendered and measured: all 5 in `TemplateManager.tsx`, all 8 in `Studio.tsx`, `studio/page.tsx`,
`posts/page.tsx`, `ExcludeToggle.tsx`, and 6 of the 15 in `posts/[id]/page.tsx` (the metric
captions, both `← Corpus` links, the header meta, `view on linkedin`, and the `No draft behind
this post` paragraph).

`Studio.tsx`'s `Lineage` block (`:470`, `:473`, `:481`), the disabled `{reason}` line (`:928`) and
the Zernio line (`:988`) render only with a draft loaded, so they were reached the only way this
pass is allowed to reach anything — **by GET**: `/studio?draft=5493` and `/studio?draft=555`
(the one draft in Zernio). Both surfaces, both themes, both viewports: **before FAIL=1 (3.33
`Drafts`/`Lineage`) TIGHT=2 (4.55); after FAIL=0, TIGHT=0, min 5.20 light / 5.25 dark.**

**Not rendered, and therefore inferred rather than measured — 16 sites:** the `Lineage` component
(`posts/[id]/page.tsx:61`, `:65`, `:68`), the `Readings` block (`:84`, `:94`, `:101`, `:108`),
`Generated from` (`:230`), `Engagement over time` (`:245`), all 6 in `RetopicForm.tsx`, and
`AddExternal.tsx:59`. The first fifteen sit inside `/posts/[id]`'s draft branch, and **no draft in
this database carries a `late_post_id`** — checked over the API, not assumed — so no post page can
render its own lineage. The sixteenth is behind the "Add a post from elsewhere" disclosure, and
this pass clicks nothing. All sixteen took the identical class swap onto the identical token on
the identical backdrop, so the colour pair is deterministic and reads 5.64 / 6.25 by construction;
that is an argument, not a measurement, and it is labelled as one. The trigger to close it is the
first draft generated from a corpus post — that page then renders all fifteen at once.

**W2b.** `TemplateManager.tsx`'s private `Badge` and its `STATUS_STYLE` map are deleted. Status now
renders through `components/ui/badge.tsx` — approved → `success`, proposed → `warning`, retired →
`neutral`. No variant was missing, so none was added. An unknown status now falls through to the
Badge's own `neutral` default instead of the old map's `?? ""`, which rendered a bare word.

| badge | light before → after | dark before → after |
|---|---|---|
| `proposed` | **4.31 → 15.17** | 10.08 → 12.19 |
| `approved` | **4.47 → 14.38** | 9.91 → 13.12 |
| `retired` | **4.34 → 5.20** | 5.76 → **5.25** |

The three light-mode failures are gone, and the two that carried a colour on the *word* now carry
`--text` on a tint, which is why they jump to 14–15:1. `retired` moves the least because
`neutral` is the one variant whose label is `--text-muted` by design; it is also the only cell in
the table that goes **down** — 5.76 → 5.25 in dark, from a 10% white fill to `--surface-2`. It
stays 0.75 clear of the floor and is now the darkest text in the whole app, which is the point of
having one token instead of three local opinions.

## The sweep after the change — 28 of 28 clean, and no near misses left

`us021-contrast2.cjs`, unchanged, over 7 routes × 2 themes × 2 viewports:
**zero elements below the AA floor on all 28 combinations.** The stronger statement is the one
worth keeping: `us021b-ratios.cjs` reports **zero elements below 4.8** as well. The minimum
contrast anywhere in the app is now **5.20:1 light / 5.25:1 dark** — the `retired` badge — where
before the change it was 2.51:1, with six distinct colour pairs under the floor and four more
between 4.5 and 4.8.

**Deliberately not changed, with the ratios that say why:**

- **`opacity-70`** — the nav links, `Explorer.tsx`'s five table cells, and `TemplateManager`'s
  row actions (`edit` / `approve` / `preview` / `retire`) and `cancel`. Measured **6.36:1 light,
  8.06:1 dark**. Well clear, and the row actions pair `opacity-70` with `hover:opacity-100` as a
  real affordance rather than as muting. Left alone.
- **`CohortTag`'s violet** — `bg-violet-500/15 text-violet-700 dark:text-violet-300` on
  `borrowed from a creator`: **5.77:1 light, 8.81:1 dark**. It is a colour on a word, which is the
  shape `badge.tsx` argues against, but violet is not a `--success`/`--warning`/`--danger` token so
  that component's measurement does not reach it, and it clears the floor with room. Recorded, not
  converted. `proven in our own posts` reads 14.13 / 12.03.
- `CohortTag`'s docstring — the reasoning for why "unrecorded" renders at all — is unchanged and
  gained a paragraph on why it is muted with a token rather than an alpha.

## Re-walk after the fix — every invariant holds, console still silent

All 7 routes × 2 themes × 2 viewports again, because `color-scheme` is global and the muting
touched rendered text on four route surfaces. `scratchpad/us021b-rewalk.cjs`, full table in
`us021-measure-fixed.json`. GET navigation and measurement only; nothing was clicked, so nothing
was written.

- `main.x` / `main.width` = **144 / 1152** on the six shell routes at 1440, in both themes.
  `/posts/95` = **336 / 768**. Unchanged from the pre-fix table.
- `document.scrollWidth` **equals the viewport on all 28** (1440 and 390), and equals
  `clientWidth` on all 28.
- The three 390px table scrollers still scroll inside their own container: `/posts` client
  **342** / scroll **736**, `/scoreboard` ×3 client **342** / scroll **752**. Contained, not
  clipped.
- `/scoreboard` still returns **15** `table` elements under `<body>` and **3** under `main`.
  Scoped to `main table`, as briefed.
- **Zero console errors, warnings, `pageerror` or `requestfailed` on all 28**, listeners attached
  before each `goto`, every route reached by a full navigation rather than by a client-side
  router transition.
- The one number that moved: `/templates` `docHeight` **19615 → 19639** at 1440 and
  **22362 → 22446** at 390. That is the shared Badge's 1px border and slightly larger padding
  across 61 rows. Not an invariant, and visible as a defect nowhere — the row still wraps within
  its card.
- Seen, not only measured: `templates-{light,dark}-{desktop,mobile}-fold-fixed.png` beside the
  pre-fix `templates-light-mobile-fold.png` that this pass cited as showing the defect by eye.
  The ghost label is now legible, and the badges read as tinted chips with dark labels.

`make check` green: **460 backend + 233 frontend**, plus `ruff`, `mypy`, `eslint` and
`tsc --noEmit`. No test changed. That is worth stating plainly rather than as reassurance:
**the suite was green before these two defects were fixed and is green after, and it would have
been green if they had never been fixed.** Neither failure was reachable from a test — one is
painted by the browser in a shadow root, the other is a composited pixel ratio no assertion in
this repo computes. The instrument that found them is the browser, and it is now written down
twice: the sweep scripts, and these numbers.

## Verdict: **PASS.** US-021 `passes: true`, and V3 merges.

Both WRONG findings are closed with measurements in both themes. Nothing new above cosmetic
appeared in the re-walk. The cosmetic list recorded above — `/templates` at 19639px with no
filter, `/scoreboard`'s 50 structurally-empty rows, the ER column's right padding, the truncating
upload label, the first-open no-op on "use the template's default" — is unchanged and stays
recorded rather than promoted.

**`closed_circuits` still reads 0**, and no code change in this version can move it. The last step
of the loop is a human publishing a generated post in Zernio; until that happens the scoreboard is
honest about having nothing to score.

**One thing seen on the way past, recorded not promoted:** on a loaded machine (load average ~50)
the frontend suite went red — five tests, all `Error: Test timed out in 5000ms`, all of them
`waitFor`-style assertions in `api.test.tsx`, `Studio.test.tsx` and `ExcludeToggle.test.tsx`, with
the whole run taking 66s instead of its usual 11s. Re-run on the same tree at a lower load: 233
passed, and `make check` exits 0. Not a defect in this change and not a flaky assertion in the
usual sense — vitest's default 5s timeout is simply within reach of machine load for those five.
Naming it so the next agent that sees exactly this red does not go hunting for a real failure.

**One bookkeeping discrepancy, reported rather than fixed:** `US-020` still reads `passes: false`
in `prd.json`, though its commit (`b00e6a7`) landed complete — 452 → 460 backend, 180 → 233
frontend, which is exactly the baseline this pass measured against — and its own message reports
the work done. The commit simply never touched `prd.json`. Certifying another slice is not this
one's call, so the flag is left alone and named here instead.
