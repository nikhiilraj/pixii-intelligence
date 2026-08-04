# Pixii Intelligence

Pixii Intelligence is a human-in-the-loop system for learning from LinkedIn content and writing
the next post. It reads existing posts, extracts reusable patterns, generates new drafts from
those patterns, and remembers both their engagement and a human's verdict. That feedback becomes
context for the next generation.

A post is modeled as three separate components:

- **Hook** — how the post opens.
- **Structure** — how the argument or story unfolds.
- **Visual** — the layout or image that accompanies it.

Every generated draft records the exact versions of all three templates that created it. That
lineage lets later engagement and human feedback reach the correct templates rather than whatever
their newest versions happen to be.

Two boundaries are fundamental:

- **It never publishes on its own.** Nothing automated — not the daily run, not the
  scheduler — can put a post in front of an audience. A person schedules or publishes it,
  deliberately, against the exact version of the words they read. That used to mean opening
  Zernio; now it can happen here, behind a switch that is off by default. The guarantee is
  the same one; see [ADR 0002](docs/adr/0002-human-publication-authority.md).
- **It does not rank templates as "best."** There is not enough lineage-attributed performance
  data yet to make that statistically reliable.

The proposed next-generation architecture—including team authentication, durable daily automation,
Teams notifications, publishing and scheduling from Pixii, compliant reference capture, deep
research, and multi-provider image generation—is documented in the
[Pixii Platform Blueprint](docs/architecture/2026-08-04-pixii-platform-blueprint.md). Its external
API assumptions are backed by the companion
[official platform research](docs/research/2026-08-04-platform-api-research.md).

---

## What it can do

- Import published posts and metrics from Zernio.
- Add external or reference LinkedIn posts manually.
- Extract proposed hook, structure, and visual templates from the corpus.
- Let a human approve, edit, version, retire, or author templates.
- Generate a complete LinkedIn draft from an idea or topic.
- Suggest templates while allowing every choice to be overridden.
- Create several variants of one idea, then keep the preferred draft.
- Regenerate text and visuals independently without changing their lineage.
- Render deterministic HTML visuals or use AI-generated imagery.
- Upload and reuse logos, screenshots, product images, and other brand assets.
- Push a chosen result to Zernio as a draft, idempotently.
- Schedule it, publish it now, or cancel a schedule — from here, once publishing is enabled.
- Detect when that draft has subsequently been published by a human.
- Synchronize metrics and preserve engagement snapshots over time.
- Record a verdict — **Worked**, **Didn't**, or **Mixed** — and its reasoning.
- Feed those human-written lessons into future generation.
- Produce capped autonomous draft batches without pushing them anywhere.
- Run that batch once per local day, claimed durably, and report it as a Teams card.
- Show every point where the workflow is waiting on a human in one Inbox.

## How to use it

If the app is already running, open [http://localhost:3000](http://localhost:3000). The Inbox is
the home screen and the daily starting point: it draws the full circuit, puts the oldest human
blockers first, and points to the largest queue.

The normal workflow is:

1. Start at **Inbox** and open the oldest waiting item.
2. Review proposals in **Templates**. Approve at least one hook, structure, and visual; retire
   patterns you do not want to reuse. Retiring preserves the historical record.
3. Open **Studio**, enter an idea, and choose templates or ask the application to suggest them.
4. Generate a draft and inspect its text, image, and exact template versions under **Lineage**.
5. Regenerate individual parts or create variants if the first result is not right.
6. Push the chosen result to Zernio. Pixii sends a draft.
7. Schedule or publish it. Studio shows a publication panel for any draft that has reached
   Zernio. Nothing fires from the button that names it: Schedule, Publish now and Cancel
   schedule each open a confirmation showing the action, the local time as typed, the
   timezone, and the UTC instant those two resolve to, alongside every command already issued
   against the draft. A command is refused if it was confirmed against an older version of the
   words. With `PUBLISHING_ENABLED` off — still the default — the panel says the capability is
   switched off rather than pretending to work, and publishing happens in Zernio by hand.
8. Synchronize metrics after the post has accumulated meaningful engagement.
9. Record a verdict and explain why it worked, did not work, or produced a mixed result.
10. Return to **Inbox** and confirm the item moved to the next gate or completed the circuit.

The verdict explanation is especially important: the note, not just the label, is the lesson
supplied to later generations.

If something looks empty, read the wording before treating it as a failure. `0`, `—`, and an empty
queue mean different things throughout the product. If the API itself is unavailable, the route
says so instead of pretending the list is empty.

---

## The idea in one picture

```mermaid
graph LR
    A[Past posts<br/>107 in the corpus] -->|extract| B[Templates<br/>hooks · structures · visuals]
    B -->|a human approves| C[Approved library]
    C -->|generate| D[Draft<br/>text + image]
    D -->|push| E[Zernio<br/>as a DRAFT]
    E -->|a human commands it| F[Live post]
    F -->|sync metrics| G[Engagement]
    G -->|a human rules on it| H[Verdict<br/>worked / didn't / mixed]
    H -->|lessons| C
```

That circle is **the circuit**. One full trip round it is **one lap**. The Inbox shows a counter
for how many laps have completed.

It currently reads **0**. Everything in the loop works and has been tested; nobody has published
a generated post yet, so the loop has never closed. That is a people problem, not a code problem.

---

## The three ideas behind it

**1. A post is a hook, a structure, and a visual.** Not one blob of text. If you separate those,
you can keep the shape that worked and change the subject. "The equation hook" or "the
offer-reward structure" become things you can reuse deliberately instead of by feel.

**2. Every draft remembers exactly what made it.** Not "hook v-latest" — *hook `stat-hero` v1,
which is now retired*. That is the whole point: when a post does well, you need to know which
version of which template produced it, or the record is worthless. Several bugs in this project
were of exactly this kind — code that resolved "the newest version" instead of "the version this
draft actually used" — and each one silently credited the wrong template.

**3. A human judges, the machine remembers.** The tool never decides a post was good. It asks
you, records your answer and your reason, and feeds your words back into the next prompt.

---

## The seven screens

| Screen | What it's for |
|---|---|
| **Inbox** (`/`) | Home. Four queues of things waiting on a human, how long each has waited, and the lap counter. If you only open one page, this is it. |
| **Studio** (`/studio`) | Write. Type an idea, pick templates or let it suggest them, generate a draft with its image. Also opens any existing draft. |
| **Corpus** (`/posts`) | Every post the tool knows about, filterable, with an engagement chart. This is the raw material. |
| **Post detail** (`/posts/[id]`) | One post: its engagement over time, the templates that produced it, and the form where you rule on it. |
| **Templates** (`/templates`) | The library. Approve or retire proposals, author new templates, edit existing ones. **This is the main bottleneck** — 50 proposals are waiting. |
| **Assets** (`/assets`) | Images that templates can use. Upload, tag, delete. |
| **Scoreboard** (`/scoreboard`) | What each template version has actually done. Mostly empty, honestly so. |

### The four Inbox queues

These are the four points where the circuit stops and waits for a person:

1. **Proposals awaiting review** — extraction suggested a template; only you can approve it.
2. **Built, awaiting push** — a draft exists locally and hasn't been sent to Zernio.
3. **Pushed, awaiting a human** — it's sitting in Zernio as a draft, waiting on a person to
   schedule or publish it. A draft already scheduled is *not* here: it is waiting on a clock,
   and a queue that promises "these are waiting on you" must not hold things you cannot act
   on.
4. **Published, awaiting verdict** — it went live; nobody has ruled on it yet.

---

## What it deliberately refuses to do

This is the part that surprises people, and it is a decision, not an omission.

Engagement across this corpus spans **12.7×** — the median post gets 525 engaged actions, the
best gets 6,691 — and each template has roughly **3 samples**. At that spread and that sample
size, any ranking you compute is noise wearing a confident face.

So the tool:

- **Never says "best", "top", or "recommended."** There is no sort-by-performance control
  anywhere, and adding one would be undoing a decision rather than adding a feature.
- **Always shows its sample count**, and flags anything under 5 as `too thin (n/5)`.
- **Prints `—`, not `0`, where data was never collected.** 35 posts in the corpus have no
  impressions data at all — they were scraped, not measured. Showing `0` would present an
  absence as a measurement. Where the database genuinely cannot tell the two apart, the tool
  says so rather than guessing.
- **Shows `Closed circuits: 0`** rather than hiding the counter until it's flattering.

The optimizer — actual ranking — is not a backlog item. It is a **threshold**: roughly 300 posts
with recorded template lineage. There are currently zero.

**Nothing in this tool ever publishes.** A draft reaches Zernio as a draft. A human publishes it
there, by hand, always.

---

## Running it

Prerequisites: Docker, Python 3.12 with `uv`, Node.js, and `pnpm`.

For a fresh checkout, install both applications' dependencies:

```bash
make install
```

Configure the variables documented in `.env.example`. Locally, `.env` is a **symlink** to the
vault's `.env`; never copy secret values into this repository. `GET /health` reports whether each
external integration is configured without exposing its credential.

Run the three components in separate terminals:

```bash
# Terminal 1 — Postgres on :5433
make up

# Terminal 2 — migrations and FastAPI on :8000
make api

# Terminal 3 — Next.js on :3000
make web
```

Then open:

- Application: [http://localhost:3000](http://localhost:3000)
- Interactive API documentation: [http://localhost:8000/docs](http://localhost:8000/docs)

Check the running services:

```bash
curl -s http://localhost:8000/health
curl -s http://localhost:8000/inbox
```

Stack: Python 3.12 · FastAPI · SQLModel · Alembic · Postgres · Next.js 16 · React 19 ·
TypeScript · Tailwind 4 · Radix · Recharts. Tests: pytest + Vitest/Testing Library.

---

## Testing it

### 1. Automated checks

Run the complete quality gate:

```bash
make check
```

This runs Ruff, mypy, ESLint, TypeScript, pytest, and Vitest. To run one side only:

```bash
cd backend && .venv/bin/pytest -q
cd ../frontend && pnpm test
```

A green suite is necessary but not sufficient; the visual and end-to-end checks below cover
things that jsdom and mocked integrations cannot prove.

### 2. Safe UI smoke test

This pass stays local except for generation or rendering calls and does not push to Zernio:

1. Visit every screen and confirm an API failure is distinguishable from an honestly empty list.
2. Approve one hook, one structure, and one visual template. Start with a text-only visual such as
   `stat-card`; an `image_url` slot needs an asset selected.
3. Generate a draft from a simple idea.
4. Confirm it contains text, a rendered visual, and three lineage entries with explicit versions.
5. Regenerate the text and verify its lineage does not change.
6. Regenerate the visual, compare it with the previous version, and restore the previous image.
7. Generate three variants, keep one, and confirm the discarded drafts leave the Inbox.
8. Upload an asset and try a visual with an image slot.
9. Exercise Corpus filters, open a post detail page, and inspect the unranked Scoreboard.

Generation and AI rendering may make paid external API calls even though this test does not push
a draft anywhere.

### 3. Browser and responsive test

Automated DOM tests cannot reliably prove layout, focus, chart sizing, or contrast. Inspect every
screen in a real browser at **390px** and **1440px**, in both light and dark modes. Check that:

- The page itself has no horizontal overflow.
- Navigation, buttons, dialogs, and keyboard focus remain usable.
- Charts have visible dimensions.
- `0`, `—`, an empty queue, and an unavailable API are presented as different states.

### 4. Full Zernio circuit

This walks one post all the way round the loop. **Steps 1–4 are yours. Step 5 is Monte's, and it
is the only step no software can do for you.**

### Before you start

```bash
curl -s http://localhost:8000/health   # required integration credentials should read true
curl -s http://localhost:8000/inbox    # four queues plus closed_circuits
```

Only continue when you intend to create a real external draft. Verify the Zernio, Azure chat, and
renderer configuration you plan to use. The push and metrics steps require Zernio credentials;
generation requires Azure chat; AI visuals require Azure image credentials.

### 1 · Approve at least one of each template kind

Open `/templates`. You need **one approved hook, one approved structure, and one approved
visual** — generation cannot run otherwise, and Studio will tell you so.

50 proposals are waiting. Read a few, approve the ones that look right, retire the rest.
Editing a template writes a **new version** rather than overwriting, so past posts keep pointing
at the wording that actually earned their numbers.

> Prefer a visual whose slots are all text. A visual with `image_url` slots needs an asset
> picked, or it renders with empty boxes. `stat-card` is the safe one.

### 2 · Generate a draft

Open `/studio`. Type an idea. Either pick a hook/structure/visual or press **Suggest templates**
and let it choose. Press **Generate draft**.

You should get post text and a rendered image, with a **Lineage** block naming the exact template
versions used. Check that block — it is the record everything downstream depends on.

Try **Write variants** too: same idea, three different template combinations, side by side. Keep
one and the others are deleted.

### 3 · Push it to Zernio

Press **Push to Zernio as draft**. The button disables itself afterwards and the draft shows its
Zernio id.

Verify it landed as a **draft**, not a live post — open Zernio and look. This is the safety
property the whole tool is built around, so check it with your own eyes at least once.

Call push a second time and confirm it returns the same draft without creating a duplicate. The
route and underlying request are idempotent, but this live check proves the external behavior.

### 4 · Confirm the Inbox moved

Reload `/`. The draft should have moved from **Built, awaiting push** to **Pushed, awaiting
Monte**. Both queues link straight to the draft in Studio.

### 5 · Monte publishes it — the step that actually closes the loop

Ask Monte to publish that specific draft in Zernio.

> **Do not delete and repost it.** That mints a new Zernio id, and the tool joins the post back
> to the draft on the original id. Delete-and-repost breaks the lineage permanently.

### 6 · Pull the metrics back

Wait for real engagement — a day at least, ideally a few.

```bash
curl -X POST http://localhost:8000/metrics/sync
```

The tool notices the draft went live, joins it to the published post, and starts recording
engagement snapshots. The post should now appear in **Published, awaiting verdict**.

### 7 · Rule on it

Open the post from that queue. You'll see its engagement curve and the templates that made it.
Record **Worked / Didn't / Mixed** and — this matters — **write the reason**. A verdict with no
note teaches the generator nothing; the note *is* the lesson.

### 8 · Confirm the lap closed

Reload `/`. **`Closed circuits` should read 1.**

Then generate another draft and check the prompt now carries your lesson. That is the loop
feeding itself, which is the entire point of the tool.

---

## Current state

```
closed_circuits:              0     ← never completed a lap
proposals awaiting review:   50     ← the bottleneck
built, awaiting push:         4
pushed, awaiting Monte:       2
published, awaiting verdict:  0

corpus:      107 posts (69 Monte, 15 creator inspiration, 13 pixii.creates, 10 Pixii_ai)
templates:   11 approved · 50 proposed · 2 retired
verdicts:    0
tests:       508 backend · 232 frontend
```

Visual templates can now be **extracted** rather than hand-authored. `POST /templates/extract/visuals`
shows a vision model the images of the posts that performed and asks for the layout underneath
them, as HTML — not as a prompt. Every proposal is rendered once before it is offered, so nothing
reaches the review queue that cannot be drawn, and the route is slow for that reason.

The brand mark is **pinned, never generated**: a slot declaring `role: "logo"` gets the real asset's
id as its `default_asset_id`, and the renderer embeds the file's own bytes. Set `BRAND_LOGO_ASSET_ID`
to the id returned by `POST /assets` and no model ever draws the logo. Leaving it unset is fine —
the slot simply has no default and the picker asks.

Extraction still proposes; a human still approves. Nothing here ranks a visual template, for the
same reason nothing ranks a hook.

The scoreboard reads empty and that is **correct**, not broken: no generated post has ever gone
live, so there is nothing to attribute.

---

## Where the bodies are buried

Things that cost someone hours. Full detail in `.scratch/v3/progress.txt`.

- **Zernio's join key is `latePostId`, not `_id`.** They are different namespaces; `_id` matches
  0 of 45 published posts.
- **Zernio's `/v1/analytics` is a 50-row window, not your account.** It also truncates silently
  two ways: over-limit returns HTTP 200 with an empty list, and unpaged returns page 1 of 4 that
  looks complete. Always check `pagination.total`.
- **Zernio reports `shares: 0` on every LinkedIn row.** It does not measure reposts. A sync used
  to overwrite real counts with zero.
- **Every datetime column is `timestamp without time zone`.** A value written as aware reads back
  naive after a round-trip, and comparing the two **raises**. Normalise through `main._utc`.
- **Lineage resolves through `generated_from(family, version)`, never "latest."** Getting this
  wrong credits the wrong template version, silently.
- **Azure image generation rejects any dimension not divisible by 16**, so 1080×1350 is
  unrequestable — ask for 1088×1360 and scale down.

---

## What is not verified, and cannot be by any test here

Written plainly because the alternative is discovering it in production.

- **The circuit has never run end to end.** Every segment is tested in isolation and proven
  against the live Zernio and Cloudflare APIs. The joins between them have never carried a real
  post all the way round.
- **Nothing has been measured against real engagement.** Every number in the scoreboard logic is
  exercised with fixtures. No verdict has ever been recorded.
- **Radix keyboard behaviour** — dialogs opening, focus traps, Escape, typeahead. jsdom only
  approximates focus, so these were checked by hand in a browser, not by tests.
- **Chart rendering.** Recharts is 0×0 in jsdom; the charts were verified by looking at
  screenshots, not asserted.
- **Skeleton geometry.** Tests compare the declared container classes between a page and its
  loading twin; only a browser can prove the widths actually match.
- **Two UI surfaces have never been seen against real data** — the verdict-retract control and
  the asset picker's "use the template's default" — because no post carries a ruling and no
  template carries a default asset. Both were photographed against a read-only proxy instead.
- **`AddExternal`** (the "add a post from elsewhere" form on `/posts`) **has no test file at
  all.** It writes to the corpus.

---

## A note on the tests

Do not read a green `make check` as proof.

`make check` did not run the frontend suite at all until this version — every frontend test
written across two versions sat outside the gate. A later audit ran **204 deliberate mutations**
against the code to see which tests noticed; 190 did. Among the ones that didn't: six assertions
checking for a `404` that passed against routes **that did not exist**, because the framework
returns 404 for any unrouted path. Two routes had no HTTP coverage whatsoever.

Three of the worst defects this project has had — a page 10,384 pixels wide with its buttons
off-screen, every page's content misaligned with the nav, and a control invisible in dark mode —
were all found by **rendering the page and looking at it**, with a fully green suite the whole
time.

If you change something here, the standard is: **break it on purpose and check a test fails.**
