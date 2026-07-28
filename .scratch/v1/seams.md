# Seam Verification — 2026-07-29

Every external integration point this spec depends on, checked against the live service or
the published reference. Verified before slicing, not assumed during it.

## ✅ Zernio analytics — `GET /v1/analytics`

Called live with `ZERNIO_API_KEY`. Returned 50 posts: 27 LinkedIn (Monte Desai), 13 YouTube
(pixii.creates), 10 X (Pixii_ai), covering 2026-04-30 → 2026-07-28.

Per-post fields confirmed present: `_id`, `latePostId`, `content`, `publishedAt`,
`scheduledFor`, `status`, `platform`, `platformPostUrl`, `mediaItems`, `thumbnailUrl`,
`mediaType`, `profileId`, `isExternal`, `isAd`, and `analytics` containing `impressions`,
`reach`, `likes`, `comments`, `shares`, `saves`, `clicks`, `views`, `follows`,
`engagementRate`, `lastUpdated`. The `platforms` array repeats metrics per account and
carries `platformPostId` (`urn:li:share:…`) and `syncStatus`.

## ⚠ Zernio pagination — two silent-failure modes

Both return **HTTP 200 with an empty post list**, not an error:

| Request | Result |
|---|---|
| `limit=50` | 27 LinkedIn posts, `pagination: {page:1, limit:50, total:27, pages:1}` |
| `limit=200` | **0 posts**, no `pagination` object, HTTP 200 |
| `fromDate=2025-08-01&toDate=2026-07-29` (>90d span) | **0 posts**, HTTP 200 |

The ingest must page with `limit=50` and must treat an unexpected empty result as a failure.
This is the exact failure shape that lets a wrong assumption survive undetected, and it has a
named regression test in the spec.

## ✅ Zernio create-post — `POST /v1/posts`

Contract read from the published API reference. Accepts `content`, `title`, `mediaItems`,
`platforms`, `scheduledFor`, `publishNow`, `isDraft`, `timezone`, `queueId`, `tags`,
`hashtags`, `mentions`, and **`metadata` (arbitrary object)**. Optional `x-request-id` header
provides idempotency. Returns `post._id`.

Draft is the default when none of `scheduledFor`, `publishNow` or `queuedFromProfile` is
supplied — "nothing auto-publishes" is native behaviour.

`POST /v1/posts/{id}/metadata` patches metadata after creation.

**RESOLVED 2026-07-29 (US-010) — the join key is `latePostId`, not `_id`.**
Created a real draft; the create response returned `_id = 6a691c6d95e614b6077edb22`, and
`GET /v1/posts` carries that same value as its `_id` with our metadata intact.

Across **all 45 published posts** (corrected 2026-07-29 — an earlier pass read only page 1
of `/v1/posts` and wrongly reported 13):

| join attempt | matches |
|---|---|
| `/v1/posts._id` → `analytics.latePostId` | **34 / 45** |
| `/v1/posts._id` → `analytics._id` | **0 / 45** |

`analytics._id` is a separate analytics-record identifier: `GET /v1/posts/{analytics._id}`
returns **404**, while `GET /v1/posts/{analytics.latePostId}` returns **200** and the same
post. **Joining on `analytics._id` would match nothing, silently.** The 11 unmatched
published posts are not a counter-example — they are outside the analytics window (see
below).

Analytics carries **no unpublished drafts at all** (0 of 103 drafts appear), so a pushed
draft only becomes joinable once it is published. The id is therefore confirmable *without
writing anything*, by cross-referencing already-published posts across the two endpoints.

## ⚠ Analytics is a window, not the account — the corpus is a subset

**Corrected 2026-07-29.** `GET /v1/posts` reports `total: 155` across 4 pages —
**103 draft, 45 published, 5 partial, 2 failed**. `GET /v1/analytics` reports `total: 50`,
one page, and only **37 of those 50 rows carry a `latePostId`**.

Consequences that were previously misstated:

- The corpus this system learns from is **the 50 analytics rows** (27 LinkedIn, 13 YouTube,
  10 X), **not the account**. There are **34 published LinkedIn posts**, so roughly 7 of
  Monte's published LinkedIn posts were never visible to template extraction.
- **11 published posts have no analytics row at all**, all dated March–April 2026 — analytics
  behaves as a recent window. Older history is not reachable through it.
- Reading `?limit=50` without paging gives page 1 of 4 and looks like the whole account. This
  is the same silent-truncation trap as the empty-list failure above, in a different costume.

To learn from the full published history, ingest would need to page `GET /v1/posts` and accept
that those rows carry no metrics.

## ✅ Account posture — external posts are synced

Every analytics row carries `isExternal: true` and `syncStatus: "synced"`. Posts Monte writes
outside Zernio do flow into analytics, so the dashboard is not limited to app-generated posts
— within the analytics window.

This is account configuration, not a platform guarantee — Zernio's documentation states that
personal LinkedIn accounts only receive analytics for posts published through Zernio. The sync
must therefore handle lineage-less posts as a normal case, which it does by design.

## ✅ Azure OpenAI chat — primary LLM

`POST {AZURE_OPENAI_CHAT_ENDPOINT}/openai/deployments/{deployment}/chat/completions`
→ **HTTP 200**. Deployment `gpt-5-5`, api-version `2024-12-01-preview`. Uses
`max_completion_tokens` (not `max_tokens`).

## ✅ Cloudflare Browser Rendering — the `html` visual renderer

`POST /client/v4/accounts/{account}/browser-rendering/screenshot` → **HTTP 200**, returned a
23,430-byte PNG at exactly **1080×1350**. Rendered `$325M` on the brand cream `#F5F0E8`
crisply at 180px — the typographic case that AI image generation cannot reliably produce.

The `html` renderer path is proven end to end.

## ✅ Azure OpenAI image — the `ai` visual renderer

Verified live 2026-07-29 (US-008). `POST {endpoint}/openai/deployments/gpt-image-2/images/generations?api-version=2025-04-01-preview`,
header `api-key`, body `{prompt, n, size, quality}` → HTTP 200 with the PNG under
**`data[0].b64_json`**. This deployment never returns a URL.

**Both dimensions must be divisible by 16.** `1080x1350` is rejected outright
("Width and height must both be divisible by 16"), so the brand size cannot be requested.
Arbitrary `/16` sizes are accepted, including `1088x1360` — exact 4:5 — which is then scaled
down to 1080x1350. No crop, no distortion.

Credentials present across five regions (default, POL, SWE, UAE, WUS3).

## ✅ Channel identifiers

`GETLATE_LINKEDIN_ID` present, plus Twitter, Instagram, Facebook, Threads, YouTube, Pinterest,
Reddit and Bluesky. V1 uses LinkedIn only; the rest are noted as provisioned, not in scope.

## ⚠ Local toolchain

| Tool | Status |
|---|---|
| Python 3.12.0 | ✅ available (system default is 3.14.0 — pin explicitly) |
| Node 22.21.1 / pnpm 10.18.3 | ✅ |
| uv 0.9.27 | ✅ |
| Docker 28.5.1 | installed, **daemon not running** |
| PostgreSQL 16 & 17 | installed via Homebrew, **not started** |

Neither Postgres path is running. The first slice must either start the Docker daemon or run
the Homebrew service; both are available, so this is a setup step and not a blocker. Pin
Python to 3.12 explicitly — the system default is 3.14 and would be picked up silently.

## Cost

No new vendor. Zernio, Azure OpenAI and Cloudflare Browser Rendering are already paid for.
Postgres is self-hosted. Added recurring cost: **$0**.
