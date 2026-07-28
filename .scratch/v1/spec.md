# Pixii Intelligence — V1 Spec

**Status:** ready-for-agent
**Date:** 2026-07-29
**Repo:** `~/pixii-intelligence` (standalone, sibling of `~/monte-workshop`)

---

## Problem Statement

Pixii's content operation is spread across ~48 tools in `monte-workshop/bots-and-tools`, of
which four carry the content load: `pixii-cli` (12,145 LOC), `podcast-chopper` (9,838),
`social-automation` (5,109) and `ai-slop-scorer` (2,721) — roughly 30,000 lines with 86 test
files. They generate posts, schedule them to Zernio, and nominally track performance.

Three problems follow from that shape.

**The system does not learn.** `pixii-cli/pixii/steps/tracking.py` states in its module
docstring that "Zernio (GetLate) exposes no GET post-analytics endpoint … so metrics come
from PostHog: total site reach + per-channel referral visitors." That statement is false as
of 2026-07-29 — `GET /v1/analytics` returns per-post impressions, reach, likes, comments,
shares, saves, clicks and engagement rate for all 50 posts across LinkedIn, YouTube and X.
Because the existing loop reads a proxy signal (site referral traffic attributed by channel)
rather than actual post performance, the "flywheel" has never had real data to turn on.

**Nothing records why a post was written the way it was.** Posts are generated freely and
scheduled with a `zernio_id`, but no record connects a published post to the hook, the
structure, or the visual pattern that produced it. Without that link, performance data cannot
be attributed to any creative decision, so no amount of accumulated analytics can improve the
next post. This is the load-bearing gap: lineage is cheap to record at creation and
impossible to reconstruct afterwards.

**There is no library of what works.** Monte's posts vary in performance by 12.7x, and the
winners share identifiable hook patterns, post structures and visual layouts. None of it is
written down. Every new post restarts from a blank page, and the knowledge lives only in
Monte's head.

The operator-level symptom: content decisions are made from intuition, the same patterns get
rediscovered repeatedly, and the tooling sprawl means no single place answers "what should we
post next, and what happened to the last one."

## Solution

**Pixii Intelligence** — one application that owns content generation, improvement and
analysis end to end. It replaces the four content tools rather than sitting beside them.

It has four moving parts.

**A template library.** Three pillars, each a versioned, human-editable artifact extracted
from Monte's real posts and reviewed before use:

- *Hooks* — 5–10 opening patterns with typed slots, derived from the winners. The corpus
  shows these are genuinely slot-shaped: `"$450 turned into $19k/mo in recurring revenue"`
  is `{small_value} turned into {large_value} {unit}`; `"On July 27, Amazon will cut your
  product titles from 200 characters to 75"` is `On {date}, {platform} will {change}
  {before} → {after}`.
- *Structures* — beginning/middle/end shapes per post type, starting with **offer-reward**
  and **deep-research**, each declaring which hook patterns pair with it.
- *Visuals* — ~5 layout templates. Each declares its own renderer: `html` for typographic
  frames (rendered deterministically to 1080×1350) or `ai` for scene and lifestyle imagery.

**A generator with two entry modes.** *Directed* — an operator supplies an idea, topic or
source and optionally picks a template; the system drafts hook, body and visual. *Autonomous*
— a scheduled run selects a topic from configured research sources, picks templates, and
produces drafts unattended. Both stamp lineage at creation and both land in Zernio as
**drafts**. Nothing publishes without a human.

**A feedback loop that actually closes.** Every generated post is stamped with
`hook_template_id`, `structure_template_id` and `visual_template_id` — written to the local
database *and* into Zernio's own `metadata` field on the post. A scheduled sync reads
`GET /v1/analytics`, joins on the Zernio post id, and attributes real performance to the
creative decisions that produced it.

**A dashboard.** A performance surface over every post — filterable, comparable, explorable —
plus a template scoreboard showing what each hook, structure and visual has actually done.

### What this is deliberately not

It is **not an optimizer**. The corpus is 27 LinkedIn posts with a 12.7x spread between the
median (525 impressions) and the maximum (6,691). Distributed across ~8 hook templates that
is roughly 3 samples per template against an order-of-magnitude noise floor. Any automatic
ranking or bandit-style selection built on that would be fitting noise and would be trusted
because it looked quantitative.

V1 therefore ships a **scoreboard with lineage**: the numbers are shown, the attribution is
recorded, and a human decides which templates to promote or retire. Automatic selection
becomes defensible at roughly 300 lineage-tagged posts, and is only reachable at all if
lineage recording starts now.

## User Stories

### Corpus and ingest

1. As a content operator, I want every post Pixii has published pulled in automatically with
   its real metrics, so that the system reasons about actual history rather than a sample.
2. As a content operator, I want post media downloaded and stored locally, so that visual
   patterns can be studied and referenced offline.
3. As a content operator, I want the ingest to page correctly through the API, so that no
   posts are silently missing.
4. As a content operator, I want re-running ingest to update existing posts rather than
   duplicate them, so that the corpus stays clean across runs.
5. As a content operator, I want to drop in posts from outside Zernio — creator posts sent
   over LinkedIn, screenshots, pasted text — so that the template library can learn from
   sources the API does not cover.
6. As a content operator, I want manually-added corpus items marked by source, so that I can
   tell Pixii's own history from external reference material.
7. As a content operator, I want ingest failures reported rather than swallowed, so that a
   silent zero-result response is never mistaken for an empty account.

### Template library

8. As a content strategist, I want hook patterns extracted from the highest-engagement posts,
   so that the library encodes what has actually worked rather than what sounds good.
9. As a content strategist, I want each extracted hook to carry its slots, its tone, and the
   posts it came from, so that I can judge whether the abstraction is honest.
10. As a content strategist, I want to review, edit, approve or reject every extracted
    template before it can be used, so that nothing enters the library unvetted.
11. As a content strategist, I want post structures defined as beginning/middle/end sections
    with guidance per section, so that a draft has a shape to follow rather than a vibe.
12. As a content strategist, I want offer-reward and deep-research structures defined in V1,
    so that the two post types we run most often are covered.
13. As a content strategist, I want each structure to declare which hooks pair well with it,
    so that the generator makes coherent combinations.
14. As a content strategist, I want visual templates with typed slots and a declared
    renderer, so that each layout is produced by the method that suits it.
15. As a content strategist, I want templates versioned, so that editing one does not
    invalidate the performance history attributed to its earlier form.
16. As a content strategist, I want to retire a template without deleting it, so that
    historical attribution survives.
17. As a content strategist, I want to author a template by hand without extraction, so that
    a new idea or a supplied design spec can enter the library directly.

### Generation — directed

18. As a content operator, I want to start a draft from an idea, topic or source link, so
    that I can turn a thought into a post without a blank page.
19. As a content operator, I want the system to suggest suitable hook and structure templates
    for my idea, so that I benefit from the library without memorising it.
20. As a content operator, I want to override any suggestion, so that my judgement wins.
21. As a content operator, I want the draft grounded in real exemplars from the corpus, so
    that it reads like Pixii rather than like a language model.
22. As a content operator, I want to edit the generated text before anything leaves the
    system, so that I control what gets published.
23. As a content operator, I want the visual generated from the chosen visual template, so
    that text and image are consistent by construction.
24. As a content operator, I want to regenerate the text or the visual independently, so that
    one bad output does not cost me the other.
25. As a content operator, I want to see which templates produced a draft while I review it,
    so that the attribution is visible rather than hidden.

### Generation — autonomous

26. As a content operator, I want a scheduled run to produce drafts without me, so that the
    pipeline keeps moving when I am busy.
27. As a content operator, I want autonomous runs to draw topics from configured sources, so
    that unattended output is grounded in something real.
28. As a content operator, I want autonomous runs to respect a cap on drafts per run, so that
    a scheduling bug cannot flood the queue.
29. As a content operator, I want autonomous drafts to be clearly marked as such, so that I
    review them with appropriate scrutiny.
30. As a content operator, I want a failed autonomous run to notify rather than fail silently,
    so that I find out from an alert and not from an empty queue.

### Lineage and publishing

31. As a content operator, I want every draft stamped with the hook, structure and visual
    template that produced it, so that its performance can be attributed later.
32. As a content operator, I want lineage written into Zernio's own post metadata as well as
    our database, so that attribution survives even if our records are lost.
33. As a content operator, I want drafts pushed to Zernio as drafts, so that nothing can
    publish without a human acting.
34. As a content operator, I want the Zernio post id captured at creation, so that the join
    key exists before the post is ever published.
35. As a content operator, I want a retried push not to create a duplicate post, so that
    transient network failures are safe.
36. As a content operator, I want a push failure surfaced with its reason, so that I can fix
    and retry rather than guess.

### Analytics and the feedback loop

37. As a content operator, I want post metrics refreshed on a schedule, so that the dashboard
    is current without manual work.
38. As a content operator, I want metrics joined to lineage automatically, so that template
    performance accrues without bookkeeping.
39. As a content operator, I want posts published outside the app included, so that the
    picture covers everything Monte posts and not only what this tool produced.
40. As a content operator, I want metric history retained rather than overwritten, so that I
    can see how a post accumulated engagement over time.
41. As a content analyst, I want posts ranked by absolute engaged actions, so that a
    high-reach post nobody responded to does not read as a success.
42. As a content analyst, I want impressions and engagement rate available as secondary
    measures, so that I can see reach and efficiency alongside volume.
43. As a content analyst, I want each template's aggregate performance and its sample count
    shown together, so that I can see when a number is too thin to trust.
44. As a content analyst, I want thin-sample templates visibly flagged, so that the scoreboard
    does not imply confidence it has not earned.

### Dashboard

45. As a content analyst, I want every post in one view with its metrics, so that I have a
    single place to look.
46. As a content analyst, I want to filter by channel, date range, post type and template, so
    that I can ask my own questions.
47. As a content analyst, I want to sort by any metric, so that I am not locked into one
    definition of good.
48. As a content analyst, I want performance over time charted, so that I can see trend
    rather than only snapshots.
49. As a content analyst, I want to open a single post and see its full text, visual, metrics
    and lineage together, so that I can study what happened.
50. As a content analyst, I want the template scoreboard to link through to the posts behind
    each number, so that I can verify an aggregate rather than trust it.
51. As a content analyst, I want the dashboard readable on a laptop screen without horizontal
    scrolling, so that it is usable in the tools I actually work in.

### Operations

52. As the engineer, I want the whole stack to start with one command locally, so that
    onboarding and debugging are not a ritual.
53. As the engineer, I want database schema changes applied by versioned migrations, so that
    environments do not drift.
54. As the engineer, I want secrets read from the environment and never committed, so that
    keys stay out of the repository.
55. As the engineer, I want the same application deployable to a server without code changes,
    so that local and hosted operation do not diverge.
56. As the engineer, I want tests to run without live API calls, so that the suite is fast and
    deterministic.
57. As the engineer, I want continuous integration running typecheck, lint and tests on every
    push, so that regressions are caught before they land.
58. As the engineer, I want external API failures handled explicitly rather than defaulting to
    empty results, so that a degraded upstream is visible.

## Implementation Decisions

### Architecture

Two deployable units and one database.

- **Backend** — Python 3.12, FastAPI, Pydantic v2, SQLModel over SQLAlchemy, Alembic for
  migrations. Serves a JSON API and hosts the scheduled jobs.
- **Frontend** — Next.js 15 (App Router), TypeScript, Tailwind, shadcn/ui, Recharts. Talks to
  the backend over HTTP; no direct database access.
- **Database** — PostgreSQL, run via Docker Compose locally and as a managed instance when
  deployed. Postgres in both places deliberately: a SQLite-local/Postgres-deployed split
  produces divergence in JSON handling and concurrency that surfaces only in production.

Rationale for the split: the ~30k LOC of existing integration logic (Gemini, Vizard, Azure,
Zernio, Graph, Sheets) is Python and ports rather than gets rewritten, and Python is the
language the ML work this system will grow into wants. The dashboard is a genuinely
interactive data surface where React earns its cost.

Scheduling starts as APScheduler inside the backend process. No Redis, no Celery, no broker
until a job demonstrably needs isolation — the V1 job set is two periodic tasks.

### Module boundaries

Each module owns one concern and is independently testable.

- `corpus` — fetching posts and metrics from Zernio, downloading media, accepting manual
  corpus drops, upserting into storage.
- `templates` — the three template kinds, their versioning and lifecycle, and LLM-assisted
  extraction proposals.
- `generation` — turning an idea plus templates into draft text and a visual; the directed
  and autonomous entry points differ only in how the idea is sourced.
- `rendering` — producing an image from a visual template; two implementations behind one
  interface, selected by the template's declared renderer.
- `publishing` — creating Zernio drafts, stamping lineage, capturing the returned id.
- `analytics` — the metrics sync and the aggregation that produces the scoreboard.
- `api` — HTTP surface; thin, delegating to the modules above.

### Integration seams

The external boundary is deliberately narrow. Every third-party call goes through a small
number of adapters, and tests exercise the modules against those adapters' interfaces rather
than the network.

- **Zernio adapter** — the single seam for all social scheduling and analytics. Verified live
  on 2026-07-29.
- **LLM adapter** — one interface for text generation, backed by Azure OpenAI chat as primary.
- **Image adapter** — one interface for generated imagery, backed by Azure OpenAI image.
- **HTML render adapter** — one interface for HTML→PNG, backed by Cloudflare Browser Rendering
  with a local headless-browser fallback.

### Verified Zernio contract

Confirmed against the live API and the published reference, not assumed:

- `GET /v1/analytics` returns, per post: `_id`, `latePostId`, `content`, `publishedAt`,
  `platform`, `platformPostUrl`, `mediaItems`, `thumbnailUrl`, `isExternal`, `status`, and an
  `analytics` object of `impressions`, `reach`, `likes`, `comments`, `shares`, `saves`,
  `clicks`, `views`, `follows`, `engagementRate`, `lastUpdated`. A `platforms` array repeats
  the metrics per connected account and carries `platformPostId` (`urn:li:share:…`).
- **`limit` caps at 50.** A larger value returns an empty post list with HTTP 200 — not an
  error. The ingest must page with `limit=50` and an incrementing `page`, and must treat an
  unexpected empty result as a failure rather than an empty account.
- **A `fromDate`/`toDate` span beyond 90 days also returns empty** with HTTP 200. Date
  filtering must stay within the supported window or be omitted.
- `POST /v1/posts` accepts `content`, `mediaItems`, `platforms`, `scheduledFor`, `publishNow`,
  `isDraft`, `timezone`, `tags`, `hashtags`, `mentions` and **`metadata` (arbitrary object)**,
  plus an optional `x-request-id` header for idempotency. It returns the created `post._id`.
  When none of `scheduledFor`, `publishNow` or `queuedFromProfile` is supplied the post
  defaults to a draft.
- `POST /v1/posts/{id}/metadata` patches metadata after creation.
- All 50 posts on this account carry `isExternal: true` with `syncStatus: "synced"`, so posts
  Monte writes outside the app are included in analytics. The documented personal-account
  restriction does not apply to this account — but the sync must not *depend* on that, since
  it is an account configuration rather than a guarantee.

### Lineage

Lineage is the mechanism the feedback loop rests on, so it is recorded twice.

- **Locally** — a generated post row holds `hook_template_id`, `structure_template_id`,
  `visual_template_id`, each with the template *version* used, plus the generation mode
  (directed or autonomous) and the Zernio post id returned at creation.
- **In Zernio** — the same identifiers are written to the post's `metadata` object at
  creation, so attribution survives loss of the local database and is visible to anyone
  inspecting the post in Zernio.

The join key is the Zernio post id captured from the create response. The analytics response
carries both `_id` and `latePostId`; the first slice that publishes a post must confirm which
of the two matches the create-time id and record the finding, since the two identifiers are
distinct in the observed payloads.

Posts that arrive through ingest with no lineage — everything historical, and anything Monte
writes outside the app — are stored with null lineage and are included in post-level
analytics but excluded from template aggregates.

### Templates

A template is a versioned record with a stable identity, a kind (`hook`, `structure`,
`visual`), typed slots, provenance (the corpus posts it was derived from), a lifecycle status
(`proposed`, `approved`, `retired`) and a version number. Editing an approved template creates
a new version rather than mutating the old one, so performance already attributed to the
earlier version stays attached to it.

Extraction proposes; a human approves. An LLM reads the corpus ranked by engaged actions and
proposes candidate patterns with the posts that support each. Nothing enters the library
without explicit approval, and every proposal shows its source posts so the abstraction can be
checked against the evidence.

Structure templates define ordered sections with per-section guidance and declare compatible
hook kinds. Visual templates declare `renderer: "html" | "ai"`; `html` templates carry a
component identifier and slot definitions, `ai` templates carry a prompt skeleton and a style
reference. Both produce 1080×1350 by default, matching the existing corpus.

### Success metric

The primary ranking measure is **engaged actions** — the sum of likes, comments, shares and
saves. It is a count rather than a ratio, so a post seen by 332 people cannot outrank one seen
by 5,747 on efficiency alone, and a post with 6,691 impressions and 11 reactions correctly
falls to the bottom. Impressions and engagement rate are retained and displayed as secondary
measures. The choice is recorded here because it determines which posts extraction learns
from, and the two obvious alternatives disagree on 60% of the top five.

### Sample-size honesty

Anywhere a template aggregate is shown, its sample count is shown with it, and aggregates
below a configured threshold are visibly marked as insufficient. The system does not rank
templates against each other in V1 and does not select templates automatically. This is a
product decision driven by the measured 12.7x variance, not a limitation to be engineered
around.

### Configuration and secrets

All credentials come from the environment. The existing catalogued keys are reused —
`ZERNIO_API_KEY`, the `GETLATE_*_ID` channel identifiers, `AZURE_OPENAI_CHAT_*`,
`AZURE_OPENAI_IMAGE_*`, `CLOUDFLARE_BROWSER_RENDERING_TOKEN`. No new vendor and no new
recurring cost is introduced. A committed `.env.example` documents every variable by name;
no `.env` is ever committed.

### Migration posture

V1 does not retire anything. It runs alongside the existing tools on the same Zernio account,
which is safe because it only creates drafts. Retirement of `pixii-cli` and `podcast-chopper`
happens in later phases once parity is reached, and `ai-slop-scorer` remains available behind
an adapter seam rather than being reimplemented.

## Testing Decisions

A good test here describes behaviour an operator could observe and would notice breaking. It
does not assert on function call order, private structure, or the shape of an intermediate
value. When a test needs to change because an implementation detail changed, it was testing
the wrong thing.

**No test performs live network calls.** Every external service is reached through an adapter
interface, and tests substitute a fake implementation. This keeps the suite fast and
deterministic and makes the API contract explicit in one place.

What gets tested, and what specifically must be covered:

- **Corpus ingest** — pagination across multiple pages; that re-ingesting the same posts
  updates rather than duplicates; and, explicitly, **that an empty response where posts were
  expected is treated as a failure**. This last case is a direct regression test for the
  discovered API behaviour where an oversized `limit` returns HTTP 200 with zero posts. It is
  exactly the failure mode that produced the stale assumption in the existing tooling.
- **Template lifecycle** — that editing an approved template produces a new version and leaves
  the prior version intact; that retirement preserves historical attribution; that a proposed
  template cannot be used for generation before approval.
- **Generation** — that a draft carries the lineage of the templates that produced it in every
  path, directed and autonomous; that an autonomous run respects its per-run cap.
- **Publishing** — that a post reaches Zernio in draft state; that lineage is present in the
  outgoing metadata; that the returned post id is persisted; that a retry with the same
  request id does not create a second post.
- **Analytics join** — that metrics attach to the correct post; that posts without lineage are
  included in post-level views and excluded from template aggregates; that engaged actions is
  computed as specified.
- **Scoreboard aggregation** — that sample counts accompany aggregates and that thin samples
  are flagged.
- **Rendering** — that an `html` template renders at the expected dimensions with slot values
  present; that renderer selection follows the template's declaration.
- **API** — request/response behaviour of each endpoint against a test database, including the
  error cases.

Frontend testing is limited in V1 to component-level tests of the data-display logic
(formatting, sorting, filtering, empty and thin-sample states). No end-to-end browser suite —
the dashboard is read-mostly and the underlying aggregations are tested on the backend where
they are computed.

There is no prior art in this repository; it is new. The Python testing conventions in
`pixii-cli` and `podcast-chopper` (pytest, fakes over mocks, one behaviour per test) are a
reasonable reference and are broadly consistent with the above.

## Out of Scope

Deferred deliberately, recorded so they are not lost:

- **Channels beyond LinkedIn.** Credentials exist for nine (X, Instagram, Facebook, Threads,
  YouTube, Pinterest, Reddit, Bluesky); V1 ships LinkedIn only. Provisioning is not scope.
- **The video pipeline** — ingest, transcription, clipping, reels. `podcast-chopper` continues
  to own this until a later phase.
- **Blog and YouTube publishing.**
- **Retiring any existing tool.** V1 adds; it does not remove.
- **Automatic template selection**, bandits, and any form of automated ranking of templates
  against each other. Blocked on sample size, not on effort.
- **Comment mining** into topic suggestions. Zernio exposes the endpoints
  (`get-inbox-post-comments`, `list-inbox-comments`); the feature is deferred, not blocked.
- **Creator watch** — periodic re-extraction of patterns from creators Monte follows.
- **Repurpose graph** — deriving per-channel variants from one insight.
- **Weekly post-mortem generation.**
- **Comment-to-DM automation.** Zernio supports it natively and it would supersede the
  existing `LinkedIn-Offer-Reward` tool, but it is a distinct feature.
- **A voice or AI-slop guard.** `ai-slop-scorer` exists; it is reached through an adapter seam
  and is not reimplemented.
- **Multi-user accounts, roles and permissions.** Single-team internal tool.
- **Automatic publishing.** Out of scope permanently by design, not by phase.

## Further Notes

**On the two missing corpus inputs.** The creator posts Monte sent over LinkedIn and the
per-post design specifications are not in the vault, the repository, or the Zernio API.
Template extraction in V1 therefore runs against the 27 LinkedIn posts that are available.
Manual corpus ingest exists specifically so both can be absorbed when supplied, without
reopening the design. No slice's acceptance depends on material that does not yet exist.

**On the stale-assumption failure mode.** The existing tracking module reads a proxy metric
because a comment recorded that no analytics endpoint existed. The endpoint does exist. The
lesson is structural, not incidental: a documented assumption about an external service ages
badly and silently. This specification records verified contracts with the date of
verification, and the ingest tests assert on the specific silent-empty-response behaviour that
makes such assumptions hard to detect.

**On scale.** The destination is one tool replacing roughly 30,000 lines across four systems.
That is a multi-phase programme. V1 is one complete vertical through every layer — it proves
the architecture end to end, and it starts recording lineage, which is the one thing that
cannot be recovered later. Everything else can be built in any order afterwards; lineage
cannot be backfilled.
