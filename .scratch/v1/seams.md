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

Across all **13** published posts on the account:

| join attempt | matches |
|---|---|
| `/v1/posts._id` → `analytics.latePostId` | **13 / 13** |
| `/v1/posts._id` → `analytics._id` | **0 / 13** |

`analytics._id` is a separate analytics-row identifier. **Joining on it would match nothing,
silently.** Analytics also does not carry unpublished drafts at all, so a pushed draft only
becomes joinable once it is published.

## ✅ Account posture — external posts are synced

All 50 posts carry `isExternal: true` and `syncStatus: "synced"`. Posts Monte writes outside
Zernio still flow into analytics, so the dashboard covers his complete posting history rather
than only app-generated posts.

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
