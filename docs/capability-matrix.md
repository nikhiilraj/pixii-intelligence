# Capability matrix

Every capability the backend has, and whether a person can reach it, see what it did, and
recover when it failed — without opening `curl` or the FastAPI docs page.

Built by enumerating the routes (`grep -rn '@router\.\|@app\.' backend/app/*.py`), the mount in
`main.py`, and the scheduler's jobs, then matching each against the frontend's actual call
sites. It is a record of a specific audit, not a design document: where a capability is
deliberately API-only, the reason is in the last column and is a decision someone can disagree
with, not a gap someone forgot.

**One thing the call-site audit gets wrong on its own, worth knowing before reading the table.**
Grepping for `getJson`/`postJson`/`postForm`/`postBlob` misses every route reached by the
browser directly — `GET /drafts/{id}/previous-visual` and the `/media` mount are both `<img
src>` attributes and both looked dead until the markup was read. Two of the rows below exist
because of that.

Legend for **Entry point**: a route means a screen in this app. **API-only** means no screen
reaches it and the last column says whether that is a decision or a finding.

---

## Corpus — what exists

| Capability | Endpoint / trigger | Entry point | Status & result shown | Recovery | Tests | API-only justification |
|---|---|---|---|---|---|---|
| List the corpus, filtered and sorted | `GET /posts` | `/posts` — Corpus | Table; a failed read renders `ApiFailureNotice`, never an empty table | Reload button on the notice | `test_api_posts.py`, `test_api_explorer.py`, `posts/Explorer.test.tsx` | — |
| One post, its numbers and its media | `GET /posts/{id}` | `/posts/[id]` | Metrics row; `—` where impressions were never collected | 404 → not-found; other failures → notice | `test_api_post_detail.py`, `posts/[id]/page.test.tsx` | — |
| Engagement over time | `GET /posts/{id}/history` | `/posts/[id]` — curve | Chart, gated on there being a draft | Failure notice in place of the chart | `test_metrics.py`, `posts/[id]/page.test.tsx` | — |
| Which draft produced a post | `GET /posts/{id}/draft` | `/posts/[id]` — lineage | Three states: draft, honestly none, failed read | Failure notice | `test_api_post_detail.py` | — |
| Add a post from elsewhere | `POST /corpus/manual` | `/posts` — Add external | Toast on refusal carrying the API's `detail` | Re-submit; the form keeps its values | `test_manual_corpus.py`, `posts/AddExternal.test.tsx` | — |
| **Bulk LinkedIn scrape import** | `POST /corpus/linkedin` | **`/operations` — Import a LinkedIn scrape** | Parsed post count before sending; `created`/`updated` after, both printed as numbers | Failure card with the API's `detail`; the paste is not cleared | `test_linkedin_scrape.py`, `operations/LinkedInImportPanel.test.tsx` | — |
| **Pull posts and metrics from Zernio** | `POST /corpus/ingest` | **`/operations` — Pull the corpus from Zernio** | Confirmation names both passes; result gives `fetched`/`created`/`updated`/`history_fetched`/`history_recovered` | Failure card; the route is an upsert, so retry is safe and says so | `test_corpus.py`, `operations/ZernioPanels.test.tsx` | — |
| Hold a post out of extraction | `POST /posts/{id}/exclude` | `/posts/[id]` — Exclude toggle | Button state; toast on refusal | Toast + retry | `test_corpus.py`, `posts/[id]/ExcludeToggle.test.tsx` | — |
| Serve a post's or asset's file | `app.mount("/media", StaticFiles)` | `/posts/[id]` image, `assetSrc()` in Assets and Studio | A broken image is the only signal | Reload | `test_media.py` | Not a JSON route; reached as an `<img src>`. Nothing to "operate". |

## Templates — the library

| Capability | Endpoint / trigger | Entry point | Status & result shown | Recovery | Tests | API-only justification |
|---|---|---|---|---|---|---|
| List templates, filtered by kind and status | `GET /templates` | `/templates`, `/posts`, `/studio` | Lists; failed read renders a notice | Reload | `test_api_templates.py`, `templates/TemplateManager.test.tsx` | — |
| Propose hooks from the corpus | `POST /templates/extract/hooks` | `/templates` — Extract hooks | Count proposed; cohort is explicit in the control | Toast on refusal | `test_extraction.py`, `TemplateManager.test.tsx` | — |
| Propose structures | `POST /templates/extract/structures` | `/templates` — Extract structures | Count proposed | Toast | `test_structures.py` | — |
| Propose visual templates from post images | `POST /templates/extract/visuals` | `/templates` — Extract visuals | Count proposed; the control says the route is slow and why | Toast | `test_visual_extraction.py` | — |
| Approve / retire a template | `POST /templates/{id}/approve`, `/retire` | `/templates` — review queue | Session counters; toast on refusal | Toast | `test_templates.py`, `TemplateManager.review.test.tsx` | — |
| Author a template | `POST /templates` | `/templates` — Author | Toast; the new row appears | Toast | `test_templates.py` | — |
| Edit a template (writes a new version) | `PUT /templates/{id}` | `/templates` — Edit | Toast; the version number moves | Toast | `test_templates.py` | — |
| Preview an unsaved template body | `POST /templates/preview` | `/templates` — TemplatePreview | PNG, or the 502 the renderer's own refusal carries | Retry | `test_template_preview.py`, `TemplatePreview.test.tsx` | — |
| Preview a saved template | `POST /templates/{id}/preview` | `/templates` — row preview | PNG or refusal | Retry | `test_template_preview.py` | — |
| **Every version of a template family** | `GET /templates/{id}/versions` | **API-only** | — | — | `test_api_templates.py` | **Decision: documented as internal API support, not connected this pass.** It is the `(family_id, version)` attribution trail the whole project rests on, and a version-history disclosure on `/templates` is the obvious home — but it is a *display* of lineage next to controls that already work, and the four operations above it were the gap. Named as unfixed, not as fine. See findings. |
| **Hooks a structure declares it pairs with** | `GET /templates/{id}/compatible-hooks` | **API-only** | — | — | **The route has none.** `test_structures.py` covers `extraction.compatible_hooks` 4 times, always at the function; nothing calls the handler, so its 400 branch is unexercised | **Decision: API-only for now, with one real consumer named.** Extraction records `compatible_hook_families` on every structure and the route resolves it to *usable* hooks. Its only sensible reader is Studio's template picker, which would narrow the hook list when a structure is chosen — and `studio/Studio.tsx` is held by another agent this session. Surfacing it anywhere else (a read-only list on `/templates`) would be a second surface built to avoid an endpoint count, which is what the README's refusals section warns against. See findings. |

## Generation — templates to drafts

| Capability | Endpoint / trigger | Entry point | Status & result shown | Recovery | Tests | API-only justification |
|---|---|---|---|---|---|---|
| Directed editorial generation | `POST /drafts/workflow` | `/studio` — Generate | Full stage, gates, readiness, research | Retry as a new auditable attempt | `test_studio_workflow.py`, `Studio.test.tsx` | — |
| Retry a failed workflow | `POST /drafts/{id}/retry` | `/studio` — Retry | New attempt, old one preserved | — | `test_studio_workflow.py` | — |
| Suggest templates for an idea | `POST /drafts/suggest` | `/studio` — Suggest templates | The three picks, overridable | Re-suggest | `test_generation.py` | — |
| Variants of one idea | `POST /drafts/variants` | `/studio` — Write variants | Spend reported on success *and* on the 502 | Refusal card carrying the spend | `test_generation.py`, `Studio.test.tsx` | — |
| Keep one variant | `POST /drafts/variants/keep` | `/studio` | Discards leave the Inbox | — | `test_generation.py` | — |
| Regenerate text / visual | `POST /drafts/{id}/regenerate-text`, `-visual` | `/studio` | Lineage unchanged; draft marked for review again | — | `test_visual_iteration.py` | — |
| Restore the previous visual | `POST /drafts/{id}/restore-visual` | `/studio` | Image swaps back | — | `test_visual_iteration.py` | — |
| Read the previous visual | `GET /drafts/{id}/previous-visual` | `/studio` — the before/after `<img>` | The image itself | — | `test_visual_iteration.py` | Not a JSON route. **Looked dead in the call-site audit and is not** — it is an `<img src>`, gated on `draft.has_previous_visual`. |
| Re-topic from a published post | `POST /drafts/retopic` | `/posts/[id]` — Retopic | Spend in words via `calls()` | Toast; 409 for a post with no draft | `test_generation.py`, `RetopicForm.test.tsx` | — |
| List / open a draft | `GET /drafts`, `GET /drafts/{id}` | `/studio` | Failed read distinguished from empty | Reload | `test_generation.py` | — |
| Legacy direct generation | `POST /drafts` | **API-only** | — | — | `test_generation.py` | **Superseded, deliberately.** Its own docstring says "legacy direct generation used by variants and existing API clients". `POST /drafts/workflow` is the reviewed path and is what Studio uses; giving this one a button would put an unreviewed generation next to a reviewed one and invite the wrong press. Kept because `/variants` shares its machinery. |
| **Capped unattended batch, on demand** | `POST /drafts/autonomous-run` | **`/operations` — Run unattended generation now** | Estimate before (`at most N chat completions`), then observed `created`/`failed`/`visuals_failed`/`topics`/`llm_calls`/`image_calls`; the 502 shows the spend the rolled-back drafts still cost | Failure card naming the reason and the spend | `test_autonomous.py`, `operations/AutonomousRunPanel.test.tsx` | — |

## Assets — the image library

| Capability | Endpoint / trigger | Entry point | Status & result shown | Recovery | Tests | API-only justification |
|---|---|---|---|---|---|---|
| Upload an asset | `POST /assets` | `/assets`, `/studio` picker | Row appears; toast on refusal | Toast | `test_api_assets.py`, `AssetLibrary.test.tsx` | — |
| List / filter assets | `GET /assets` | `/assets`, `/studio` | Failed read distinguished from empty | Reload | `test_api_assets.py` | — |
| Delete an asset | `DELETE /assets/{id}` | `/assets` — with confirmation | Confirmation names what uses it | Toast | `test_api_assets.py` | — |
| **Promote a post's media into the library** | `POST /assets/promote` | **`/posts/[id]` — Promote this image to Assets** | Confirmation with kind and tags; the created asset's label, kind and real dimensions | Toast carrying the API's 422 (`no downloaded media`, or a video) | `test_asset_promotion.py`, `posts/[id]/PromoteMedia.test.tsx` | — |

## Publication — out

| Capability | Endpoint / trigger | Entry point | Status & result shown | Recovery | Tests | API-only justification |
|---|---|---|---|---|---|---|
| Push a draft to Zernio as a draft | `POST /drafts/{id}/push` | `/studio` — Push | Idempotent; the Zernio id appears | Toast | `test_publishing.py` | — |
| Schedule / publish / cancel | `POST /drafts/{id}/schedule`, `/publish`, `/cancel-schedule` | `/studio` — PublishPanel | Confirmation showing action, account, local time, zone, resolved UTC, revision | Five distinct refusals, one of which offers a reload | `test_distribution.py`, `PublishPanel.test.tsx` | — |
| The audit trail of commands | `GET /drafts/{id}/publications` | `/studio` — Commands issued | Failed read says so; it is not an empty history | Reload | `test_distribution.py` | — |
| Where a command would go, and whether it may | `GET /publishing` | `/studio` — PublishPanel banner | Kill switch stated before a command is composed | — | `test_publishing_target.py` | — |

## Metrics and evidence — back in

| Capability | Endpoint / trigger | Entry point | Status & result shown | Recovery | Tests | API-only justification |
|---|---|---|---|---|---|---|
| **Sync engagement now** | `POST /metrics/sync` | **`/operations` — Sync engagement from Zernio** | `fetched`/`snapshots`/`went_live`/`created`/`updated`, all printed as numbers | Failure card with Zernio's own refusal | `test_metrics.py`, `operations/ZernioPanels.test.tsx` | — |
| Per-template-version evidence | `GET /metrics/templates` | `/scoreboard` | Sample counts; `too thin (n/5)`; **never ranked** | Failure notice | `test_scoreboard.py`, `scoreboard/page.test.tsx` | — |
| Record a verdict | `POST /posts/{id}/verdict` | `/posts/[id]` — Verdict form | Note cap enforced client- and server-side | Toast | `test_verdicts.py`, `VerdictForm.test.tsx` | — |
| The four human gates and the lap counter | `GET /inbox` | `/` — Inbox | Ages, counts, `Closed circuits: 0` shown rather than hidden | Failure notice | `test_inbox.py`, `(inbox)/page.test.tsx` | — |
| Liveness, credential presence, ceilings | `GET /health` | `/` footer, `/studio`, **`/operations`** | Per-credential health lights; `variants_max`; **`autonomous_max_drafts`** | Failure leaves controls enabled — unknown is not off | `test_health.py`, `operations/page.test.tsx` | — |

## Research — evidence behind a draft

| Capability | Endpoint / trigger | Entry point | Status & result shown | Recovery | Tests | API-only justification |
|---|---|---|---|---|---|---|
| **Every research run** | `GET /research` | **`/operations` — Research runs** | Newest first, never sorted; `failed` badged so it cannot hide; a failed read never draws the empty-list copy | Reload button on the notice | `test_api_research.py`, `operations/ResearchHistory.test.tsx` | — |
| **One dossier** | `GET /research/{job_id}` | **`/operations?job=N`**, and `/studio` for a draft's own run | Sources, claims, citations, unknowns, spend — `DossierPanel`, shared with Studio | 404 is rendered as a refusal, not as "nothing selected" | `test_research.py`, `ResearchPanel.test.tsx`, `ResearchHistory.test.tsx` | — |

## Unattended work with no HTTP route at all

These have no endpoint to connect. They are listed because a matrix built from routes alone
would report full coverage while the two things that run without anyone asking stay invisible.

| Capability | Trigger | Entry point | Status & result shown | Recovery | Tests | Justification |
|---|---|---|---|---|---|---|
| Periodic metrics sync | `scheduler.run_metrics_sync`, every `METRICS_SYNC_HOURS`, gated on `ENABLE_SCHEDULER` | **None** — log line only | Nothing on screen | Run `/operations` → Sync metrics by hand | `test_metrics.py` (the function), none for the schedule | **Finding, not a decision.** See below. |
| Publication reconciliation | `reconcile.reconcile_publications`, inside the same tick | **None** — log line only | Nothing on screen; a reconciled command's row does change in `/studio` | Reload the draft | `test_reconcile.py` | Same finding. |
| Daily editorial slot | `scheduler.tick_daily_slot` → `daily.run_daily_slot`, gated on `ENABLE_AUTONOMOUS` | **None** — its drafts appear in the Inbox | Nothing says a run happened, or failed | `/operations` → Run generation now | `test_daily.py` | Same finding. |
| Teams notification | `notify.notify`, called by `run_autonomous` and `daily.notify_run` | **None** | Nothing; the webhook is the only output | — | `test_daily.py` | Configuration, not an operation. Nothing to press. |
| Publishing kill switch | `PUBLISHING_ENABLED` | `/studio` states it; **not settable from the UI** | The panel says publishing is off up front | — | `test_publishing.py` | **Deliberate.** ADR 0002: turning publishing on is a decision made in configuration, by a person, out of band. A UI toggle for it is the thing the ADR exists to prevent. |

---

## Findings

Things this audit surfaced. Not all of them are fixed, and the ones that are not are listed
because a matrix whose only purpose is to make itself look complete is worth nothing.

### Fixed by this pass

1. **Four operations had no UI at all** — `POST /corpus/ingest`, `POST /metrics/sync`,
   `POST /corpus/linkedin`, `POST /drafts/autonomous-run`. The README's own end-to-end walk
   told you to run `curl -X POST http://localhost:8000/metrics/sync` in step 6, in the middle
   of an otherwise-clickable circuit. All four are now on `/operations`, behind confirmations.
2. **`POST /assets/promote` had no caller**, while 62 files sat in `media/` and the library
   started empty — so the asset picker had nothing real to offer until someone uploaded a
   second copy of an image the app already held. Now on `/posts/[id]`, beside the image.
3. **`GET /research` and `GET /research/{job_id}` were unreachable except through a draft.**
   The list route's own docstring argues that browsing runs is its own need; nothing browsed
   them. Now on `/operations`, selectable with `?job=`.
4. **`/health` did not carry `autonomous_max_drafts`.** Any client saying what an autonomous
   run would spend had to hardcode the ceiling and drift the day the setting changed — the
   exact failure `health()`'s docstring names, which `variants_max` exists to prevent for
   Studio. Added as a sibling scalar, with a test comparing it to the setting and not to a
   literal.

### Surfaced and not fixed

5. **`GET /templates/{id}/compatible-hooks` has exactly one sensible consumer and it is not
   built.** Structure extraction records `compatible_hook_families`; the route resolves it to
   usable hooks; Studio's picker offers every approved hook regardless of the structure
   chosen. The pairing a model was asked to record is thrown away at the point it would be
   used. `studio/Studio.tsx` was held by another agent this session, so this is named rather
   than done — and the fix belongs in that picker, not in a second read-only surface built
   somewhere easier to reach.

   **The route also has no HTTP test.** Every assertion about pairings is against
   `extraction.compatible_hooks` directly; nothing exercises the handler, so its 400 for a
   non-structure is unexercised. This is the same class of gap the README's 204-mutation audit
   found twice ("two routes had no HTTP coverage whatsoever") and is why this cell says so
   rather than naming the file that happens to contain the word.
6. **`GET /templates/{id}/versions` is unread.** Lineage resolves through `(family_id,
   version)` everywhere in this system, and the one route that shows a family's history has no
   reader. A disclosure on the `/templates` review row is the natural home. Left for the same
   reason: it is a display, and the operations above were the gap.
7. **Nothing in the UI shows that unattended work ran — but there is now a route to
   connect.** The metrics scheduler, the reconciliation pass and the daily editorial slot all
   produce log lines and nothing else. A daily run that has been failing for a week is
   invisible from every screen — the Inbox would simply have fewer drafts in it, which looks
   like a quiet week. The half of that which was a *missing route* is closed:
   `GET /daily-runs` reads the `DailyRun` rows, newest first, passing NULL counts through as
   null because `0` would say a run finished and produced nothing. The screen is not built.
   `/operations` is where it belongs — that page already holds the work that is not writing —
   and until it exists this remains invisible to anyone not holding a terminal. Task #17.
8. **`POST /drafts` (legacy) is reachable and unreviewed.** Not a UI gap — a deliberate
   omission, recorded here so that nobody "connects" it later on the grounds that it appears
   in this table with no entry point.
9. **`AddExternal` still has no test file**, as the README says. Outside this pass's scope;
   restated because it writes to the corpus, which is what extraction learns from, and it is
   the only mutation in the app with no coverage at all.

### Not gaps

- `/media` and `GET /drafts/{id}/previous-visual` are reached by the browser as image
  sources. A call-site audit that greps for `getJson`/`postJson` reports both as dead. They
  are not.
- `GET /publishing` looks like a duplicate of `/health` and is not: `/health` reports booleans
  and scalars only, because the Inbox footer renders `credentials` row-per-key as a health
  light, and `account_id` is a string.
