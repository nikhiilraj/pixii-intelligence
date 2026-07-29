# Pixii Intelligence — handoff

**State as of 2026-07-29.** Read this first in a fresh session, then `progress.txt`
(`## Codebase Patterns` especially) and `.scratch/v1/seams.md`. Those hold live-verified API
contracts that are **not** guessable from documentation — do not re-derive them.

## Where things stand

**V1 is complete.** 15/15 slices pass, 185 tests, ruff / mypy / tsc / eslint / build all
green, 20 commits, nothing uncommitted.

```
make up      # Postgres on :5433
make api     # backend on :8000
make web     # frontend on :3000
make check   # full gate
```

Current data: 51 posts, 22 template versions, 4 drafts, 100 metric snapshots.

## What exists

| Area | State |
|---|---|
| Corpus ingest (Zernio + manual paste) | done |
| Template library (hook / structure / visual, versioned, approve-retire) | done |
| Extraction — 6 hooks, 2 structures from the real corpus | done, approved |
| Visual rendering — `html` (Cloudflare) and `ai` (Azure) | done |
| Directed generation with lineage | done |
| Push to Zernio **as draft** | done |
| Scheduled analytics sync + snapshot history | done |
| Post explorer + template scoreboard | done |
| Autonomous runs (capped, opt-in, never pushes) | done |

## The five facts that cost the most to learn

1. **Join key is `latePostId`, not `_id`.** `Draft.zernio_post_id == Post.late_post_id`.
   `analytics._id` is a different namespace — matches **0/45**. A test asserts they differ.
2. **`/v1/analytics` is a 50-row window, not the account.** `/v1/posts` holds 155 (103 draft,
   45 published). Extraction saw 27 LinkedIn posts; 34 are published. **The corpus is a
   subset.**
3. **The API truncates silently two ways.** Over-limit → HTTP 200 empty list. Unpaged → page
   1 of 4 that looks complete. Always check `pagination.total` / `pages`.
4. **Drafts never appear in analytics** (0 of 103). A pushed draft is unjoinable until
   published. Not a bug.
5. **Azure image gen rejects any dimension not divisible by 16**, so 1080×1350 is
   unrequestable — ask 1088×1360 and scale down.

## Open items for Nikhil

- Supply the creator posts + per-post design specs — paste at `/posts`. Six hooks from 27
  posts is thin; this is the cheapest quality jump available.
- Review the 6 hooks / 2 structures approved on his behalf to demonstrate the chain.
- Delete test draft `6a691c6d95e614b6077edb22` (tagged `pixii-intelligence`).
- **The real test:** have Monte publish one generated post. Until then the scoreboard is
  correctly all zeros and the system can say nothing.

## V2 — in dependency order

1. **Widen the corpus.** Page `GET /v1/posts` for the full published history (34 LinkedIn,
   not 27), accepting that those rows carry no metrics. Cheapest real improvement.
2. **Asset picker.** Visual templates with `image_url` slots cannot complete unattended —
   there is no source of product cutouts or logos. Today only text-only visuals work
   end-to-end.
3. **Channels beyond LinkedIn.** X and Reddit first; credentials for all 9 already exist.
4. **Comment mining.** Zernio exposes `get-inbox-post-comments`; audience questions become
   next-post topics.
5. **Creator watch.** Periodic re-extraction as new creator posts arrive.
6. **Video path.** The `podcast-chopper` replacement — ingest, transcription, clipping.
7. **Retire the old tools.** Only after parity; `ai-slop-scorer` stays behind an adapter
   seam rather than being reimplemented.
8. **Optimizer — NOT before ~300 lineage-tagged posts.** Measured 12.7x engagement variance
   means ranking before then is fitting noise. This is a product decision, not a backlog item.

## Working rule for future sessions

**Do the work in separate sessions and agents.** This build ran in one context and it got
long. Spawn an agent per slice or per investigation and keep the main session as the
orchestrator — brief it, receive the result, decide. Give each agent the pointer to this file
plus `progress.txt` and `seams.md`; that is enough context to work from without inheriting
the whole history.
