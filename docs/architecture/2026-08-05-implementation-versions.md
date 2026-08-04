# Implementing the blueprint: three versions

**Status:** Agreed plan
**Companion:** [Platform Blueprint](2026-08-04-pixii-platform-blueprint.md) ·
[Platform API research](../research/2026-08-04-platform-api-research.md)
**Last reviewed:** 2026-08-05

The blueprint is a multi-month program: 6 phases, 19 epics, a 4-person team, and an
estimated 25–43 weeks. This file cuts it to what one person can actually ship, in three
versions, and — more importantly — names what is **not** being built and what would make it
worth building.

The versions are ordered by *what closes one lap*, not by the blueprint's phase numbers.
That is the binding constraint: the circuit has never completed once. A monorepo restructure
does not move that number.

---

## v0 — data safety (do first, blocks nothing else)

**Why first:** `.scratch/corpus-widening/` is tracked in a **public** repository
(`nikhiilraj/pixii-intelligence`, verified public 2026-08-05). 156 files, including 12
creator images and Monte's LinkedIn post text. Blueprint §2 flagged it; it is live.

**Decision taken:** purge from history.

- `git filter-repo --path .scratch/corpus-widening/ --invert-paths`, force-push, add to
  `.gitignore`.
- Write `docs/adr/0001-public-corpus-material.md` recording what was removed and why a
  delete commit was not sufficient.
- **Known ceiling:** a history rewrite does not reach existing clones, forks, or GitHub's
  cached unreferenced blobs. Ask GitHub Support to garbage-collect if the material must
  truly be unreachable.

Everything else in the blueprint's Phase 0 (backup/restore harness, sanitized demo dataset,
secret scanning) is **deferred** — see "Not building".

---

## v1 — close one lap

**Outcome:** a daily run happens at a real time, the team hears about it in Teams with a
link straight to the draft, and the run's outcome survives a restart.

Most of blueprint §9 already runs: `autonomous.py` proposes topics and generates capped
drafts, `publishing.py` pushes a draft idempotently, `metrics.py` reconciles `went_live_at`
and accumulates snapshots, Inbox shows every human gate. Three things are missing.

### 1. The daily slot is durable

`scheduler.py` uses `BackgroundScheduler` with an **interval** trigger. "Every 24h" is
measured from process start, so on a machine that restarts, the run drifts or never fires,
and two processes fire it twice. Nothing records that today's run happened.

- One table, `daily_run`: `run_date` + `slot` (unique together), `status`, `started_at`,
  `finished_at`, `drafts_created`, `visuals_failed`, `error`, `notified_at`.
- Switch to `CronTrigger` at a configured local hour, plus a catch-up check on startup and
  on each tick: if today's row is absent, claim it (`INSERT … ON CONFLICT DO NOTHING`) and
  run. The unique constraint *is* the lock.
- `ponytail:` one table, no workflow engine, no outbox, no queue. The blueprint's 12-state
  machine buys nothing while every transition happens inside one process in one minute.

### 2. Teams delivery actually works

`notify.py` posts `{"text": …}` to an Office 365 connector webhook. Microsoft disabled
those 2026-05-18–22 (research doc §2). The current notifier is almost certainly writing to
a dead URL and logging nothing, because it swallows every exception by design.

- **Named external dependency, not a code task:** someone with tenant rights creates a
  Power Automate / Teams Workflow with an authenticated HTTP trigger restricted to a single
  identity. Anonymous "Anyone" triggers are not acceptable — the URL is a bearer credential.
- `notify.py` gains `notify_card(...)` posting an Adaptive Card: state label, hook preview,
  draft count, visuals-failed warning, and an `Action.OpenUrl` deep link. Keep `notify()`
  for plain operational lines.
- New setting `pixii_base_url` so the link is `…/studio?draft=<id>` and not a guess.
- Delivery is deduplicated by `daily_run.notified_at`, written only after Teams accepts the
  card — so a refused delivery is retried on the next tick and a successful one is never
  repeated. That nullable timestamp is the whole retry mechanism; no delivery table.
- **Unknown ≠ zero applies here too:** the card prints `—` for a count nobody took, not `0`.

### 3. The card links to the Inbox — not to one draft

**Changed during implementation.** The plan said Studio would accept `?draft=`. A run
produces up to `autonomous_max_drafts`, and `RunResult` carries counts rather than the ids,
so a single-draft deep link would be wrong most days. The card opens the Inbox, which the
README already calls the daily starting point and which sorts oldest-waiting first.

v1 therefore changes no frontend code at all. A `/studio?draft=N` link when a run makes
exactly one draft is worth adding the day `RunResult` keeps its ids — the `ponytail:` note
in `daily.notify_run` says so.

### Not in v1

Publishing, scheduling, auth, object storage, restructure. v1 changes no external effect
that does not already exist.

### Done when

A run at the configured hour writes a `daily_run` row, generates drafts, and posts one
card. A process killed mid-run neither generates a second batch nor loses its card: a
later tick finds the row still `running`, buries it past `STALE_RUN_HOURS`, and sends the
failure — because a claim that blocks the retry *and* the notification is a scheduled job
failing in silence, which is the thing this slice exists to end.

**Status: shipped 2026-08-05.** Nine mutations checked, each fails a test — removing the
conflict guard, stamping `notified_at` regardless of delivery, keying the slot on the UTC
date, printing `0` for an uncounted run, never burying a stale row, burying a live one,
dropping the per-draft detail, restoring the dead connector payload shape, and reporting an
unconfigured webhook as delivered. 547 backend tests, 243 frontend.

**Not verified:** no card has been delivered to a real Teams channel. That needs the
Workflow trigger below to exist first.

---

## v2 — schedule and publish from Pixii

**Outcome:** an authorized human schedules or publishes without opening Zernio.

### The rule this changes

`CLAUDE.md` says *"Never publish. A draft reaches Zernio as a draft. A human publishes it
there, by hand. Nothing in this codebase may change that."* Blueprint §4 deliberately
revises it to **"never publish without an explicit authorized human command in Pixii."**

**Decided 2026-08-05:** build the capability; treat *localhost + single operator* as the
authorization boundary and skip Entra ID. Recorded in `docs/adr/0002-human-publication-
authority.md`. The `Never publish` paragraph in `CLAUDE.md` is edited in the **same commit**
as the capability — not before, not after.

**Ceiling, stated plainly:** the day this runs anywhere but localhost, or a second person
uses it, authentication and a publisher role become mandatory before the publish route may
be reachable. The ADR says so and the kill switch below is how you buy time.

Blueprint §4 forbids shipping publish without confirmation, audit, idempotency,
stale-revision rejection, and a kill switch **together**. That is one slice, not five.

### Slices

1. **Zernio base-URL migration.** `zernio_base_url` is still `getlate.dev`. The documented
   base is `https://zernio.com/api/v1`. Change behind adapter contract tests **and** a live
   probe against the real account — the probe is a `! curl` line for the operator to run,
   not something this codebase runs unattended.
2. **`update_post`.** `zernio.py` has `create_post`, `upload_media`, `list_posts` and no
   PUT. Scheduling is `PUT /v1/posts/{postId}` with `isDraft: false`, `scheduledFor`,
   `timezone`. `scheduledFor` alone leaves a draft in draft state — the trap the research
   doc calls out. Publish-now is the same PUT with `publishNow: true`.
3. **`publication` table.** `draft_id`, `draft_revision`, `action`
   (`save_remote_draft` | `schedule` | `publish_now` | `cancel_schedule`),
   `requested_local_time`, `timezone` (IANA), `scheduled_utc`, `idempotency_key` (unique),
   `state`, `zernio_post_id`, `attempts`, `last_error`.
   - Three time columns, not one. Every column in this database is
     `timestamp without time zone`, so the resolved UTC value goes through `main._utc` and
     the local time and IANA zone are stored beside it verbatim. **Do not** start the
     timestamptz migration here — that is blueprint ADR #8 and a separate decision.
   - `ponytail:` `attempts`/`last_error` on the row instead of a `publication_attempt`
     table. One command's history is a counter and a string until someone needs the third.
4. **Stale-revision rejection.** `Draft` has no `revision` column. Add one, bump on every
   edit, require it on the command, reject a mismatch with 409 and the current revision.
5. **Confirmation, audit, kill switch.** A confirm step in the UI showing account, action,
   local time, timezone and resolved UTC before it fires. An append-only `audit_event`
   row (action, draft id, revision, before/after state, result, timestamp) for every
   command. A `publishing_enabled` setting that stops external commands without stopping
   generation.
6. **Reconciliation.** Poll nonterminal publications on the existing metrics tick and
   resolve drift. `ponytail:` polling, not webhooks — webhooks need a public URL and HMAC
   verification, and there is no public URL. Add them when there is one.

### Done when

Ten retries of one schedule command produce exactly one Zernio post; an ambiguous timeout
reconciles to the original id rather than creating a second; a stale revision is refused;
the kill switch blocks the command and lets generation continue. Each proved by breaking it.

---

## v3 — the research and evaluation brain

**Outcome:** generated content is planned, source-backed when it makes factual claims,
evaluated against a visible rubric, and traceable.

Chosen over visual production 2026-08-05. Blueprint §11–12. Slices, in order:

1. **Editorial brief + angle/claim plan.** Replace the one-shot writing prompt with two
   artifacts. `generation.py` keeps its interface; the stages go behind it.
2. **Prompt registry.** Prompts move out of module constants into versioned files with
   input/output schemas. A generation trace records prompt version, model deployment,
   parameters, input artifact ids, output hash, usage, and retries.
3. **Hardened fetcher.** HTTPS only, DNS/IP validation on every redirect, private and
   link-local addresses blocked, size/time/MIME limits. Fetched content is delimited as
   **quoted evidence, never instructions** — blueprint invariant 7. This is the security
   surface of v3 and it is not where laziness applies.
4. **Research dossier.** `research_job`, `research_source`, `claim`, `citation`. `none` /
   `light` / `deep` modes; `light` is *required* when the brief depends on current facts,
   numbers, named organizations, or recommendations. One search provider behind a port.
5. **Hard gates + rubric.** Deterministic gates first (schema validity, uncited factual
   claim, length, near-duplicate against recent corpus, visual-slot completeness). Then the
   versioned 100-point craft rubric returning evidence per deduction.
6. **Revision loop + budgets.** Revise against named findings with a hard iteration and
   spend ceiling enforced server-side. `SpendMeter` already exists and extends to this.
7. **Research UI.** Sources, contradictions, unknowns, claim coverage on the draft screen.

**Two rules survive v3 unchanged.** The rubric grade means *ready for editorial review*,
never *likely to perform* — blueprint invariant 6. And **never rank**: §9 step 6 permits
picking a default review candidate by craft grade, and that is all. No sort-by-performance
control over templates. The threshold is still ~300 posts with recorded lineage; there are
still zero.

### Done when

An A-grade candidate passes every hard gate, every externally verifiable claim maps to a
citation fetched in that run, and a prompt change produces a comparable evaluation report
against a baseline. Build the evaluation dataset **before** tuning prompts.

---

## Not building in v1–v3 — and what would change that

Naming the cut is what makes this a plan rather than a quiet 80% narrowing.

| Blueprint asks for | Add when |
|---|---|
| Monorepo `apps/` + `packages/` restructure | A second deployable exists, or two people edit the same module weekly. `backend/app/` is 6,072 lines across 25 files. |
| Entra ID, orgs, memberships, roles | A second user, or the app leaves localhost. Whichever comes first — this is the v2 ADR's stated ceiling. |
| Object storage for media | The app runs on more than one machine. `config.py` already carries the `ponytail:` note saying exactly this. |
| Service Bus, workers, transactional outbox | A job outlives a deploy or needs isolation. `scheduler.py` already carries this note. Two periodic jobs do not justify a broker. |
| Zernio lifecycle webhooks | There is a public HTTPS URL to receive them. Until then, polling reconciliation. |
| MV3 capture extension | **Never for LinkedIn** — both docs are unambiguous, and it needs an official permitted path plus written legal approval. For other sites: when someone actually asks. |
| OpenTelemetry / App Insights | A failure takes more than one `grep` of the logs to explain. |
| Multi-provider image routing (Gemini + gpt-image-2) | v3 was the research brain. Revisit after it lands — this is the runner-up, not a discard. |
| `timestamptz` migration | Blueprint ADR #8. A separate decision with its own migration plan; v2 works around it with explicit columns. |
| Backup/restore harness, demo dataset, secret scanning | Backup before the first irreplaceable data exists — realistically alongside v2. Secret scanning is cheap and can ride v0. |

---

## ADRs this plan requires

Written before the corresponding implementation becomes irreversible:

1. `0001-public-corpus-material` — v0, what was purged and why.
2. `0002-human-publication-authority` — v2, localhost as the authorization boundary, and
   the conditions that void it.
3. `0003-research-provider-policy` — v3, which search provider, source retention, and the
   Bing-grounding compliance caveat from the research doc §6.

---

## What this plan does not promise

Three versions is not the blueprint. It is the subset that closes the loop, then makes
publishing safe, then makes the writing defensible. Visual production, the extension, and
the platform layer are real work that is deliberately not scheduled — not forgotten.
