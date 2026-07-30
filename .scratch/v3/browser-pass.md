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
