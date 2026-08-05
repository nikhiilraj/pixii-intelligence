# Capability matrix

Every capability the backend has, and whether a person can reach it, see what it did, and
recover when it failed — without opening `curl` or the FastAPI docs page.

Built by enumerating the routes (`grep -rn '@router\.\|@app\.' backend/app/*.py`), the mount in
`main.py`, and the scheduler's jobs, then matching each against the frontend's actual call
sites. It is a record of a specific audit, not a design document: where a capability is
deliberately API-only, the reason is in the last column and is a decision someone can disagree
with, not a gap someone forgot.

**Revised after the workflow, verification and observability work landed.** The audit was taken
when generation was one synchronous request and `POST /drafts` was still what `/variants`
called. Three things changed underneath it and are corrected below: generation returns at
`planning` and is watched by polling rather than waited on, four of the five paths that write a
draft now run the same reviewed workflow, and `GET /daily-runs` exists. Findings 1–4 stay as
they were recorded; findings 5–9 are re-checked against the tree rather than carried forward.

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
| Hooks a structure declares it pairs with | `GET /templates/{id}/compatible-hooks` | `/studio` — the hook select, once a structure is chosen | The hook list **grouped** under a heading naming the structure, with every other approved hook under its own; a read that failed, a pairing that resolves to nothing, and a read still out are three different sentences beside the control | The list stays complete in all three, so there is nothing to recover from | `test_api_templates.py` (the handler, including its 400 and 404), `studio/Studio.pairing.test.tsx` | — |

## Generation — templates to drafts

| Capability | Endpoint / trigger | Entry point | Status & result shown | Recovery | Tests | API-only justification |
|---|---|---|---|---|---|---|
| Directed editorial generation | `POST /drafts/workflow` | `/studio` — Generate | **Asynchronous.** Answers at `planning`; Studio polls `GET /drafts/{id}` and names each stage as the run commits it. Ends with gates, verification, readiness and research on screen | Retry as a new auditable attempt; a reload recovers the run from the id in the address bar; `WORKFLOW_TIMEOUT` fails a run whose process died | `test_studio_workflow.py`, `test_workflow.py`, `Studio.polling.test.tsx`, `ReviewPanel.test.tsx` | — |
| Choose the research depth | `IdeaIn.research_mode` on workflow, variants and retopic | `/studio` — Auto / None / Light / Deep | The requested depth, the floor, and the signals that produced it. A request below the floor is refused before a draft row exists | Raise the depth and resend; it can never be lowered, and never silently changed either way | `test_research.py`, `test_studio_workflow.py`, `Studio.depth.test.tsx` | — |
| Check the finished post's assertions | `app.verification`, inside the workflow | `/studio` — Review panel | Every assertion the post makes and what stood behind it; `null` and an empty list are said differently | Uncited and contradicted assertions block as `failed_review`, with the words still readable | `test_verification.py` | Not a route. Runs inside `generate_reviewed_draft` and is read off the draft. |
| Retry a failed workflow | `POST /drafts/{id}/retry` | `/studio` — Retry | New attempt, old one preserved | — | `test_studio_workflow.py` | — |
| Suggest templates for an idea | `POST /drafts/suggest` | `/studio` — Suggest templates | The three picks, overridable | Re-suggest | `test_generation.py` | — |
| Variants of one idea | `POST /drafts/variants` | `/studio` — Write variants | One complete reviewed workflow per combination, so a variant may arrive `failed_review`. Spend reported on success *and* on the 502, including search calls | Refusal card carrying the spend | `test_generation.py`, `Studio.test.tsx` | — |
| Keep one variant | `POST /drafts/variants/keep` | `/studio` | Discards leave the Inbox | — | `test_generation.py` | — |
| Regenerate text / visual | `POST /drafts/{id}/regenerate-text`, `-visual` | `/studio` | Lineage unchanged; draft marked for review again | — | `test_visual_iteration.py` | — |
| Restore the previous visual | `POST /drafts/{id}/restore-visual` | `/studio` | Image swaps back | — | `test_visual_iteration.py` | — |
| Read the previous visual | `GET /drafts/{id}/previous-visual` | `/studio` — the before/after `<img>` | The image itself | — | `test_visual_iteration.py` | Not a JSON route. **Looked dead in the call-site audit and is not** — it is an `<img src>`, gated on `draft.has_previous_visual`. |
| Re-topic from a published post | `POST /drafts/retopic` | `/posts/[id]` — Retopic | Reviewed workflow, inheriting the source's exact `(family_id, version)` and drawing with the renderer that row declares. Spend in words via `calls()` | Toast; 409 for a post with no draft | `test_generation.py`, `test_renderer_selection.py`, `RetopicForm.test.tsx` | — |
| List / open a draft | `GET /drafts`, `GET /drafts/{id}` | `/studio` | Failed read distinguished from empty | Reload; `GET /drafts/{id}` also sweeps stalled runs on read | `test_generation.py` | — |
| Deprecated direct generation | `POST /drafts` | **API-only** | — | — | `test_generation.py`, `test_review_boundary.py` | **Deprecated and refused downstream, deliberately.** One completion: no brief, no angle, no research, no gates, no rubric. It is marked `deprecated=True` so `/docs` says so, sets no stage, and therefore produces `unreviewed` drafts that the push and publication routes both refuse. It is no longer what `/variants` calls — that ran the reviewed workflow as of Stage C — so this is the last unreviewed creation path rather than one of four. Giving it a button would put an unreviewed generation next to a reviewed one and invite the wrong press. |
| **Capped unattended batch, on demand** | `POST /drafts/autonomous-run` | **`/operations` — Run unattended generation now** | Reviewed workflow per topic, and `created` counts what the workflow concluded rather than what was written. Estimate before (`at most N chat completions`), then observed `created`/`failed`/`visuals_failed`/`topics`/`llm_calls`/`image_calls`/`search_calls`; the 502 shows the spend the rolled-back drafts still cost | Failure card naming the reason and the spend | `test_autonomous.py`, `operations/AutonomousRunPanel.test.tsx` | — |

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
| Push a draft to Zernio as a draft | `POST /drafts/{id}/push` | `/studio` — Push | Idempotent; the Zernio id appears; `pushed_revision` records what Zernio is holding | Toast. An `unreviewed`, `failed` or `failed_review` draft is refused, and the button says so rather than waiting to be refused | `test_publishing.py`, `test_review_boundary.py` | — |
| Schedule / publish / cancel | `POST /drafts/{id}/schedule`, `/publish`, `/cancel-schedule` | `/studio` — PublishPanel | Confirmation showing action, account, local time, zone, resolved UTC, revision. Schedule and publish send the confirmed `content`; cancel never does | Five distinct refusals, one of which offers a reload. Refused if the draft is not review-ready, or if its revision has drifted from `pushed_revision`; both guards exempt cancel, which is the remedy for the state they exist for | `test_distribution.py`, `PublishPanel.test.tsx` | — |
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

**Both of these read empty against the live database, and that is a fact about the database
rather than about the screens.** `GET /research` returns `[]`: the reviewed workflow has never
completed a real run here, so no dossier exists outside a test. Studio's research panel has
only ever been rendered against fixtures.

## Unattended work with no HTTP route at all

These have no endpoint to connect. They are listed because a matrix built from routes alone
would report full coverage while the two things that run without anyone asking stay invisible.

| Capability | Trigger | Entry point | Status & result shown | Recovery | Tests | Justification |
|---|---|---|---|---|---|---|
| Periodic metrics sync | `scheduler.run_metrics_sync`, every `METRICS_SYNC_HOURS`, gated on `ENABLE_SCHEDULER` | **None** — log line only | Nothing on screen | Run `/operations` → Sync metrics by hand | `test_metrics.py` (the function), none for the schedule | **Finding, not a decision.** See below. |
| Publication reconciliation | `reconcile.reconcile_publications`, inside the same tick | **None** — log line only | Nothing on screen; a reconciled command's row does change in `/studio` | Reload the draft | `test_reconcile.py` | Same finding. |
| Daily editorial slot | `scheduler.tick_daily_slot` → `daily.run_daily_slot`, gated on `ENABLE_AUTONOMOUS` | **None** — its drafts appear in the Inbox | Nothing on any screen says a run happened, or failed. `GET /daily-runs` reads the `DailyRun` rows and no screen calls it | `/operations` → Run generation now | `test_daily.py` | Same finding, now half-closed: the route exists, the screen does not. Task #17. |
| **Read the daily run log** | `GET /daily-runs` | **API-only** | Newest first; NULL counts pass through as null, because `0` would say a run finished and produced nothing | — | `test_daily.py` | **Finding, not a decision.** Built so that finding 7 stops being unanswerable, and left unconnected because `/operations` was already the largest new screen in that pass. Returns `[]` against the live database — no daily run has ever executed here. |
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
   **Closed by task #18, and what the live data says changed the shape of the fix.** The route
   answers, and now has HTTP tests: the newest approved version of a paired family, an empty
   list for a pairing whose hooks have all been retired, an empty list for a structure that
   recorded none, the 400 for a hook and the 404 for a missing id. Nothing was broken.

   The live library is what ruled out filtering. Both approved structures record exactly one
   family each — `ai-workflow-giveaway` → `ai-time-value-equation`, `case-study-loop` →
   `small-input-big-recurring-result` — so each resolves to **one of the six approved hooks**. A
   filter would hide five of six on every use, which makes the "show me all of them" escape
   hatch the state a reader lives in, which is a filter nobody wanted plus a control to undo it.
   Studio therefore **groups**: the recorded pairing under a heading naming the structure, every
   other approved hook under its own, all of them selectable, and choosing a structure never
   moves a hook already chosen. The wording says what extraction recorded and never that one
   hook is better — compatibility is a structural fact from the corpus, and 40 of the 61
   templates here cite a single source post.
6. **`GET /templates/{id}/versions` is unread.** Lineage resolves through `(family_id,
   version)` everywhere in this system, and the one route that shows a family's history has no
   reader. A disclosure on the `/templates` review row is the natural home. Left for the same
   reason: it is a display, and the operations above were the gap.

   **Still open.** It has HTTP tests (`test_api_templates.py`) and no frontend call site.
7. **Nothing in the UI shows that unattended work ran — but there is now a route to
   connect.** The metrics scheduler, the reconciliation pass and the daily editorial slot all
   produce log lines and nothing else. A daily run that has been failing for a week is
   invisible from every screen — the Inbox would simply have fewer drafts in it, which looks
   like a quiet week. The half of that which was a *missing route* is closed:
   `GET /daily-runs` reads the `DailyRun` rows, newest first, passing NULL counts through as
   null because `0` would say a run finished and produced nothing. The screen is not built.
   `/operations` is where it belongs — that page already holds the work that is not writing —
   and until it exists this remains invisible to anyone not holding a terminal. Task #17.

   **Still open, and re-checked rather than carried forward.** No `page.tsx` calls
   `/daily-runs`; `GET /daily-runs` returns `[]` against the live database, so nobody would
   currently see anything on that screen either.
8. **`POST /drafts` is reachable and unreviewed.** Not a UI gap — a deliberate omission,
   recorded here so that nobody "connects" it later on the grounds that it appears in this
   table with no entry point.

   **Narrowed since this was written.** It is now the *only* unreviewed path: variants, retopic
   and the autonomous run all moved to `generate_reviewed_draft`. It is marked `deprecated=True`
   in OpenAPI, its drafts take the `unreviewed` stage default, and the push and publication
   routes both refuse them — so the omission is no longer the only thing standing between it
   and an audience. It is documented in `README.md` for the same reason it is listed here:
   an undocumented bypass is worse than a documented deprecated route.
9. ~~**`AddExternal` still has no test file**~~ — **fixed before this audit and recorded
   wrongly.** `frontend/src/app/posts/AddExternal.test.tsx` has three tests and landed in
   `1ebe094`. The README carried the same stale claim. Three tests on a form that writes to the
   corpus is a floor rather than coverage, which is worth saying, but "no test file at all" was
   simply not true when it was written.

### Not gaps

- `/media` and `GET /drafts/{id}/previous-visual` are reached by the browser as image
  sources. A call-site audit that greps for `getJson`/`postJson` reports both as dead. They
  are not.
- `GET /publishing` looks like a duplicate of `/health` and is not: `/health` reports booleans
  and scalars only, because the Inbox footer renders `credentials` row-per-key as a health
  light, and `account_id` is a string.
