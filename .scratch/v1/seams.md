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

**Open item for the publishing slice:** the analytics payload carries both `_id` and
`latePostId` and they differ. The first slice that creates a real post must determine which
one the create response's `_id` matches, and record the finding. Until then the join key is
treated as unresolved between two candidates, not as known.

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

## ◻ Azure OpenAI image — the `ai` visual renderer

Credentials present across five regions (`AZURE_OPENAI_IMAGE_*` for default, POL, SWE, UAE,
WUS3) with `AZURE_OPENAI_IMAGE_DEPLOYMENT` and a `_15` variant. Not called live — no
generation was needed before the renderer slice. Verified as configured, not as working; the
`ai` renderer slice must exercise it first.

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
