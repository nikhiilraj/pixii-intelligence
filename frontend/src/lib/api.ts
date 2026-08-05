export const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

// `Verdict` in backend/app/models/post.py. One human's ruling on one published post. Sent as
// the plain string value; FastAPI coerces it into the StrEnum, and an unknown value is refused
// with a 422 naming the three allowed ones rather than being coerced into one of them.
export type Verdict = "worked" | "didnt" | "mixed";

/** `VERDICT_NOTE_MAX` at backend/app/main.py:265 — the cap the verdict route enforces.
 *
 *  The column underneath is an unbounded String, so this number is the whole contract, and it
 *  lives here rather than in the form because it is the API's limit and not the textarea's
 *  preference. The form holds the same line so a long note is stopped while it is being
 *  written, instead of coming back as a 422 after the human has finished thinking. */
export const VERDICT_NOTE_MAX = 500;

export type Post = {
  id: number;
  zernio_id: string;
  platform: string;
  content: string;
  published_at: string | null;
  platform_post_url: string | null;
  account_username: string | null;
  media_type: string | null;
  thumbnail_url: string | null;
  local_media_path: string | null;
  is_external: boolean;
  excluded_from_extraction: boolean;
  impressions: number;
  reach: number;
  likes: number;
  comments: number;
  shares: number;
  saves: number;
  engagement_rate: number;
  engaged_actions: number;

  // A human's ruling on how the post landed, and why. A `null` verdict *is* the fourth Inbox
  // queue — a published post nobody has judged yet — so absence is a normal state, not a gap.
  // `GET /posts` and `GET /posts/{id}` answer with the raw row (unlike `DraftOut`), so these
  // three arrive on the wire already and need no hand-mapping.
  verdict: Verdict | null;
  verdict_note: string;
  verdict_at: string | null;
};

/* `PostRow` — `Post & { late_post_id }` — was here, and it is gone with US-019. Its whole
 * justification was "the one field only the detail page reads": the detail page joined
 * `Draft.zernio_post_id == Post.late_post_id` in the browser over `GET /drafts?limit=500`.
 * `GET /posts/{id}/draft` does that join in the database, so no page reads `late_post_id` any
 * more and a type declaring it would be describing a consumer that no longer exists. The
 * backend still returns the column; nothing here asks about it. */

export type TemplateKind = "hook" | "structure" | "visual";
// The body of work a template was read from — `Cohort` in backend/app/extraction.py.
// Sent as the plain string value; FastAPI coerces it into the StrEnum.
export type Cohort = "voice" | "inspiration";
export type TemplateStatus = "proposed" | "approved" | "retired";

export type Template = {
  id: number;
  family_id: string;
  version: number;
  kind: TemplateKind;
  name: string;
  status: TemplateStatus;
  body: Record<string, unknown>;
  slots: Record<string, unknown>[];
  provenance: string[];
  notes: string;
};

// `AssetKind` in backend/app/models/asset.py. A kind is a filter, not a permission.
export type AssetKind = "logo" | "product" | "screenshot" | "brand" | "photo";

export type Asset = {
  id: number;
  // Basename under the /media mount: served at `${API_BASE}/media/assets/${filename}`.
  filename: string;
  label: string;
  kind: AssetKind;
  tags: string[];
  // The dimensions of the file on disk, after any downscale — not of what was uploaded.
  width: number;
  height: number;
  sha256: string;
  source_post_id: number | null;
  created_at: string;
};

/** Where the backend serves an asset's file, from the `/media` mount.
 *
 *  Here rather than in a component because two pages need it — the library grid and the
 *  Studio picker's preview — and importing it from `assets/AssetLibrary` would pull that
 *  whole client component into Studio's bundle for one template string. */
export function assetSrc(asset: Asset): string {
  return `${API_BASE}/media/assets/${asset.filename}`;
}

export type LineageEntry = { family: string; version: number; name: string } | null;

/** Every state a draft's generation can be in — the frontend half of the contract in
 *  backend/app/models/stage.py.
 *
 *  Written out as a value, not just a union type, so a test can compare it against the
 *  backend's own list. A type alone disappears at compile time and proves nothing about what
 *  the server actually sends; `test_stage.py::test_the_declared_stages_are_exactly_the_enums`
 *  holds the other end. Order matches the enum's declaration order deliberately — the pair is
 *  compared element by element, so adding a stage to one side fails on the other. */
export const STAGES = [
  // Nothing reviewed this draft — legacy `POST /drafts`. Not actionable, which is the point.
  "unreviewed",
  // In flight.
  "planning",
  "researching",
  "drafting",
  "verifying",
  "revising",
  "evaluating",
  "rendering",
  // Terminal.
  "ready",
  "failed",
  "failed_review",
] as const;

export type GenerationStage = (typeof STAGES)[number];

/** Whether a human's review may act on this draft — push, schedule or publish it.
 *
 *  One predicate, mirroring `models/stage.review_ready`, because the alternative was in
 *  Studio: `stage === "failed" || stage === "failed_review"` written out at each button. The
 *  server is still the only thing that enforces it; this decides what a control looks like. */
export function reviewReady(stage: GenerationStage): boolean {
  return stage === "ready";
}

/** Whether Retry may start a new attempt from this draft. Both failures, nothing else. */
export function retryable(stage: GenerationStage): boolean {
  return stage === "failed" || stage === "failed_review";
}

/** Whether a workflow is still working on this draft — what Studio polls against. */
export function inFlight(stage: GenerationStage): boolean {
  return (
    stage === "planning" ||
    stage === "researching" ||
    stage === "drafting" ||
    stage === "verifying" ||
    stage === "revising" ||
    stage === "evaluating" ||
    stage === "rendering"
  );
}

export type Draft = {
  id: number;
  idea: string;
  mode: string;
  hook_text: string;
  body_text: string;
  full_text: string;
  visual_values: Record<string, string>;
  // Slot name -> asset id, for the visual's `image_url` slots. A separate field from
  // `visual_values` because the renderer, the delete guard and the Zernio metadata all have to
  // tell "the number a model wrote" from "the file a human picked".
  asset_values: Record<string, string>;
  visual_error: string | null;
  visual_png: string | null;
  has_previous_visual: boolean;
  zernio_post_id: string | null;
  // What a publication command has to be confirmed against — `Draft.revision` counts
  // human-visible changes only. Required on every schedule/publish/cancel body and given no
  // default anywhere in this file for the reason `ScheduleIn` gives on the backend: a client
  // that may omit it can publish words nobody approved.
  revision: number;
  lineage: { hook: LineageEntry; structure: LineageEntry; visual: LineageEntry };
  /** Where the draft is in the review workflow — `GenerationStage` in
   *  backend/app/models/stage.py, and `STAGES` below is this file's half of that contract.
   *
   *  **Required, not optional.** `DraftOut` has always filled these five fields on every
   *  response; marking them `?` here made every consumer write a `?? "historical"` fallback,
   *  and that fallback is a lie about a `POST /drafts` row created a second ago. A genuinely
   *  historical draft is one whose `editorial` is null, which is a different question. */
  generation_stage: GenerationStage;
  generation_error: string | null;
  gate_results: { gate: string; detail: string }[];
  readiness_result?: {
    rubric_version: string;
    prompt_name: string;
    prompt_version: string;
    readiness_points: number;
    decision: "ready_for_editorial_review" | "needs_revision";
    summary: string;
    deductions: { criterion: string; points: number; reason: string; evidence: string }[];
  } | null;
  revision_rounds?: number;
  editorial?: {
    brief: {
      id: number;
      objective: string;
      audience: string;
      desired_action: string;
      constraints: string[];
      requested_mode: string | null;
      recommended_mode: string;
      research_mode: string;
      mode_signals: string[];
      prompt: { name: string; version: string };
    };
    angle: {
      id: number;
      thesis: string;
      tension: string;
      audience_stake: string;
      cta: string;
      beats: string[];
      prompt: { name: string; version: string };
    };
    planned_claims: { id: number; text: string }[];
    research_job_id: number | null;
    research: Dossier | null;
    research_error: string | null;
    write_prompt: { name: string | null; version: string | null };
    correlation_id: string | null;
  } | null;
};

/** The three things a human can command against a post that already exists in Zernio —
 *  `ACTIONS` in backend/app/models/publication.py. */
export type PublicationAction = "schedule" | "publish_now" | "cancel_schedule";

/** `GET /publishing` — where a command would go, and whether it may go at all.
 *
 *  Its own route rather than keys on `/health`, which states the rule that would break:
 *  presence flags and scalar ceilings, never a value. The Inbox footer renders `credentials`
 *  row-per-key as a health light, so a string in there draws a junk boolean.
 *
 *  `account_id` is **an opaque Zernio id, not a display name** — the label lives in Zernio
 *  behind an accounts call this app does not speak. Render it as an identifier, never as a
 *  person. `null` means `GETLATE_LINKEDIN_ID` is unset, which is a real state (pushes fail),
 *  and is not the same as the read having failed. */
export type PublishingTarget = {
  enabled: boolean;
  platform: string;
  account_id: string | null;
};

/** `PublicationOut` (api_drafts.py) — one command and what became of it. `GET
 *  /drafts/{id}/publications` returns these newest first, and that list *is* the audit trail;
 *  there is no separate audit table.
 *
 *  **The three time fields are `null` for `publish_now` and `cancel_schedule`, which name no
 *  future time.** That is an absence, not a time of midnight, and it renders as `—`.
 *  `attempts` is the opposite case: a row is committed before the first attempt is counted, so
 *  `0` there is a measurement and printing it as `—` would hide a command that was written
 *  down and never sent. */
export type Publication = {
  id: number;
  draft_id: number;
  draft_revision: number;
  action: PublicationAction;
  requested_local_time: string | null;
  timezone: string | null;
  scheduled_utc: string | null;
  // `requested` — written down, not yet sent. `accepted` — Zernio took it. `failed` — Zernio
  // refused. None of the three claims the post is live; that is `Draft.went_live_at`.
  state: "requested" | "accepted" | "failed";
  attempts: number;
  last_error: string | null;
  created_at: string;
  accepted_at: string | null;
};

/* --- research ---------------------------------------------------------------------------- */

/** `CLAIM_STATUSES` in backend/app/models/research.py — what the evidence did to one claim.
 *
 *  **`unsupported` is a recorded state, not an empty citation list.** `research.py` restructured
 *  a table so that "nothing supports this" is a value you can filter on rather than a silence a
 *  reader has to notice, and a client that infers it from `supporting_citation_ids.length === 0`
 *  undoes that: a `refuted` claim has an empty supporting list too, and calling one two sources
 *  actively contradict "uncited" says the opposite of what happened to it. Read `status`. */
export type ClaimStatus = "supported" | "disputed" | "refuted" | "unsupported";

/** `supports` or `contradicts`. A contradiction is a citation, not a failure. */
export type Stance = "supports" | "contradicts";

/** One page a research run actually fetched — `SourceOut` in backend/app/api_research.py.
 *
 *  **Fetched means read, never verified.** `trust_tier` and `published_at` are `null` on every
 *  row this application writes — nothing assigns a tier, and the fetcher's parser never reads
 *  the attributes a publication date lives in — so they render as `—`, and a fallback of
 *  `"unknown"` would put a measurement on screen that nobody took. `publisher` is the hostname
 *  of the final URL and no masthead was ever read.
 *
 *  **Link `url`, never `requested_url`.** `url` is the address that served the bytes, after
 *  redirects; a citation naming the requested one cites a page nobody read. Both are here so a
 *  reviewer can see the hop, and they are equal when there was none. */
export type ResearchSource = {
  id: number;
  url: string;
  requested_url: string;
  title: string | null;
  publisher: string | null;
  published_at: string | null;
  fetched_at: string;
  trust_tier: string | null;
  // sha256 of the bytes as served — what lets a reviewer re-fetch and prove whether they are
  // reading what the model read. The only sense in which any of this is checkable.
  content_hash: string;
};

/** The words in one source that bear on one claim — `CitationOut`. */
export type Citation = {
  id: number;
  claim_id: number;
  source_id: number;
  stance: Stance;
  span: string;
  // The source's hash when this span was taken, which can differ from the source row's own if
  // that page were ever re-fetched. That difference is the audit trail doing its job.
  source_content_hash: string;
};

export type Claim = {
  id: number;
  text: string;
  status: ClaimStatus;
  supporting_citation_ids: number[];
  contradicting_citation_ids: number[];
};

/** Something the run could not settle. `claim_id` is `null` for a question the model raised and
 *  made no claim about — both are unknowns, only one has a row to look at. */
export type Unknown = { text: string; claim_id: number | null };

/** What the run cost. **Every field is `number | null` and the null is the point.**
 *
 *  `null` is "that step never ran"; `0` is a measurement. `queries: 0` is a search loop that
 *  issued nothing, `null` is a `none`-mode run where there was no loop. So render with an
 *  explicit `=== null` test: `{spend.queries || "—"}` prints `—` for a measured zero, which is
 *  the same absence-as-measurement error read backwards, and it looks correct. */
export type ResearchSpend = {
  queries: number | null;
  sources_found: number | null;
  sources_fetched: number | null;
  llm_calls: number | null;
  // Which ceiling stopped the run early — `"queries"`, `"fetches"`, `"seconds"` — or `null` when
  // none did. "Four sources because the fetch ceiling bit" and "four sources is all there were"
  // are the same number and a different fact.
  budget_exhausted: string | null;
};

/** `GET /research/{job_id}` — one run: what it asked, what it read, what it concluded.
 *
 *  `unknowns` and `contradictions` overlap `claims` deliberately; every unsupported claim is in
 *  two of them. The lists are in the run's own insertion order and **must not be re-sorted** —
 *  `research.dossier()`'s docstring refuses ordering by status or support for the reason the
 *  project's never-rank rule gives. Prominence on screen is a badge and a summary, not a sort. */
export type Dossier = {
  job_id: number;
  question: string;
  // What ran, and what the floor detector said was needed. Both, because one field would make
  // "the system asked for light and the run did none" unanswerable afterwards.
  mode: string;
  recommended_mode: string;
  mode_signals: string[];
  // `completed` with five unsupported claims is a **success** — the run found out that nothing
  // supports them. `failed` is the run that died, and the dossier does not carry its reason.
  state: string;
  researched_at: string;
  freshness_days: number | null;
  sources: ResearchSource[];
  claims: Claim[];
  citations: Citation[];
  unknowns: Unknown[];
  contradictions: Claim[];
  spend: ResearchSpend;
};

/** One row of `GET /research` — enough to recognise a run by, and nothing it concluded.
 *
 *  No counts, deliberately: two counts side by side read as a comparison of the runs, and there
 *  is nothing here to compare. Newest first is a chronology, not a ranking. */
export type ResearchJobSummary = {
  job_id: number;
  question: string;
  mode: string;
  recommended_mode: string;
  state: string;
  researched_at: string;
};

/** `MetricSnapshot` in backend/app/models/metric.py, as `GET /posts/{id}/history` returns it —
 *  one reading of one post's numbers, oldest first.
 *
 *  Readings accumulate rather than overwrite, so this is the only place the *shape* of a post's
 *  engagement lives; the Post row keeps the latest values only. Note what the numbers are not:
 *  `impressions` is `0` on every scraped row because Zernio never measured it there, not
 *  because nobody saw the post, so a zero here is an absence and must not be plotted as a
 *  measurement. `captured_at` is naive — every datetime column in this app is `timestamp
 *  without time zone`. */
export type MetricSnapshot = {
  id: number;
  post_id: number;
  captured_at: string;
  impressions: number;
  reach: number;
  likes: number;
  comments: number;
  shares: number;
  saves: number;
  clicks: number;
  views: number;
  engagement_rate: number;
  engaged_actions: number;
};

/* `InboxItem` / `InboxQueue` / `Inbox` in backend/app/main.py:309-349.
 *
 * `id` is the id of whatever the gate acts on — a template, a draft, a post — so the queue an
 * item came from is also what says which page clears it.
 *
 * `age_days` is whole days, floored, never negative, measured from `waiting_since`. It is
 * computed server-side and rendered as given: a count says a queue is non-empty, an age says
 * the circuit stalled, and that is the only thing on this page that distinguishes work in
 * progress from work forgotten.
 *
 * A queue carries no rate, no mean and no ranking, and the queues are not comparable with each
 * other. Nothing off this route may be rendered as performance. */
export type InboxItem = {
  id: number;
  // A handle for recognising the thing, not the thing itself — the backend caps it at 80 chars.
  label: string;
  waiting_since: string;
  age_days: number;
};

export type InboxQueue = { count: number; items: InboxItem[] };

export type Inbox = {
  proposals_awaiting_review: InboxQueue;
  built_awaiting_push: InboxQueue;
  pushed_awaiting_monte: InboxQueue;
  published_awaiting_verdict: InboxQueue;
  /** Laps already completed — a draft that went live whose post now carries a verdict. Not a
   *  queue: nothing waits behind it. A count of laps and never a score; it says whether the
   *  machine has run end to end, not how anything performed. 0 today, and rendering that 0
   *  rather than hiding it is the whole reason the field exists. */
  closed_circuits: number;
};

/** `GET /health`. Read by the Inbox footer and by the Studio page.
 *
 *  `variants_max` is a **sibling** of `credentials`, not a key inside it, and the shape is
 *  load-bearing: the footer renders `credentials` row-per-key as a health light, so a number
 *  in there would draw a junk one. It is the ceiling `POST /drafts/variants` clamps to, and
 *  the client reads it only to *say* what a run will spend — the server is still the only
 *  thing that applies it. */
export type Health = {
  status: string;
  database: boolean;
  credentials: Record<string, boolean>;
  variants_max: number;
  /** `settings.autonomous_max_drafts` — the ceiling `POST /drafts/autonomous-run` clamps its
   *  `cap` query parameter to. A sibling of `credentials` for the same reason `variants_max`
   *  is, and read for the same purpose: the operations screen states what a run will spend
   *  and the server is still the only thing that limits it. */
  autonomous_max_drafts: number;
};

/* --- operations ---------------------------------------------------------------------------
 *
 * What the four unattended-work routes answer with. Every number on all four is a **measured
 * zero** when it is zero: the run happened and created nothing, appended nothing, recovered
 * nothing. None of them is ever `—`, and `value || "—"` over any of these is the
 * absence-as-measurement rule read backwards — the mistake `measured()` in ResearchPanel
 * exists to prevent, made in the other direction and looking correct all the while. */

/** `POST /corpus/ingest` — the two Zernio passes, counted separately because they are two
 *  different reads. `/analytics` carries metrics for a recent 50-row window; `/v1/posts` is
 *  the full history and reports none, so `history_recovered` is posts the window missed. */
export type IngestResult = {
  fetched: number;
  created: number;
  updated: number;
  history_fetched: number;
  history_recovered: number;
};

/** `POST /metrics/sync` — `sync_metrics` in backend/app/metrics.py.
 *
 *  `snapshots` is readings appended, not posts changed: engagement accumulates rather than
 *  overwrites, so a sync that finds identical numbers still writes a row and that is the
 *  point. `went_live` is drafts this run noticed had been published by a human — the
 *  transition the fourth Inbox queue is built on. */
export type MetricsSyncResult = {
  fetched: number;
  snapshots: number;
  went_live: number;
  created: number;
  updated: number;
};

/** `POST /corpus/linkedin` — `upsert_linkedin_posts`. Matched on normalised content, not the
 *  URN, so re-ingesting the same scrape updates rather than duplicates. */
export type LinkedInIngestResult = { created: number; updated: number };

/** `POST /drafts/autonomous-run` — what one capped unattended batch produced and spent.
 *
 *  `visuals_failed` counts drafts that exist and have no picture, and it is reported beside
 *  `created` rather than folded into `failed` because those drafts are real and openable —
 *  "3 created" with the images missing is the failure that made the backend report it.
 *
 *  The spend pair arrives on the **failure** path too, in the 502's `detail`
 *  (`{error, llm_calls, image_calls}`), because the drafts roll back with the request and the
 *  money does not. `messageFrom` already unflattens that shape. */
export type AutonomousRunResult = {
  created: number;
  failed: number;
  visuals_failed: number;
  topics: number;
} & Spend;

/** What a paid route reports it spent, on top of whatever it produced — `RetopicOut` and
 *  `VariantsOut` both carry exactly this pair, and `POST /drafts/variants`'s 502 detail
 *  carries it too, because the drafts roll back with the request and the money does not.
 *
 *  Hoisted here with `calls` when the second consumer arrived: two surfaces report a spend
 *  and both have to word it identically. */
export type Spend = { llm_calls: number; image_calls: number };

/** "1 chat completion", "2 chat completions", "0 image renders" — the observed count, in
 *  words, never a price. The meter counts calls; nothing in this app knows what a call cost.
 *
 *  In `lib/api` rather than in either component that says it: it was written twice, in Studio
 *  and in `RetopicForm`, only because a second agent held this file at the time. One wording
 *  of a spend, in one place. */
export function calls(n: number, unit: string): string {
  return `${n} ${unit}${n === 1 ? "" : "s"}`;
}

/* The result of a request, where failing is not the same as having nothing.
 *
 * The previous helper returned `T | null`, so a network error, a 500, a 404 and a
 * legitimately empty list were one value at the call site. Every page built on it could
 * only ever say "nothing here yet" — including while the backend was down. Three outcomes
 * are the minimum needed to tell the truth:
 *
 *   ok            — the request arrived and the body parsed
 *   http          — it arrived and was refused; `status` and the API's own `detail` survive
 *   network       — it never arrived; there is no status to report
 *
 * ponytail: a discriminated union and three thin functions, not a client class. No retry,
 * no caching, no request cancellation, no generated SDK. The ceiling: every read is a
 * `cache: "no-store"` server-component fetch and every mutation is a click, so there is no
 * request this cannot express. Reach for a real data layer when something needs polling,
 * optimistic updates or shared client-side cache — none of which exists yet.
 */
export type ApiFailure =
  | {
      ok: false;
      kind: "http";
      status: number;
      message: string;
      /* FastAPI's own `detail`, parsed and unflattened, beside the sentence `messageFrom` made
       * of it. A fourth fact, added for one consumer that cannot do its job without it: the
       * publication routes answer 409 two ways — `{error, current_revision}` when the draft
       * moved under the reviewer, and a plain string when it was never pushed — and only the
       * first can offer a reload. Both arrive here as status 409, and `messageFrom` renders the
       * object as "…, current_revision: 5", so the number survives only as text. Telling them
       * apart off `message` means regexing a sentence, which is the `[object Object]` class of
       * bug this module's other comments exist to prevent.
       *
       * `unknown`, never a typed shape: `detail` is a string, a list of `{loc,msg}` or an
       * object depending on the endpoint and on whether FastAPI or a handler raised it, and a
       * type asserting one of those would be a lie the compiler enforces. `null` when the body
       * was not JSON at all — a proxy answering 502 with HTML. */
      detail?: unknown;
    }
  | { ok: false; kind: "network"; message: string };

export type ApiResult<T> = { ok: true; data: T } | ApiFailure;

/** FastAPI's `detail` is a human-written string on a raised HTTPException and a list of
 *  `{loc, msg, type}` objects on a validation error. Both shapes come off the same
 *  endpoint — `/posts` answers `?sort=nope` with a string and `?source=nope` with a list —
 *  so handling only one renders `[object Object]` to the user for the other. The strings
 *  are already written for a reader; they are surfaced rather than replaced. */
function messageFrom(detail: unknown, status: number): string {
  if (typeof detail === "string" && detail) return detail;

  if (Array.isArray(detail) && detail.length > 0) {
    const parts = detail.map((item) => {
      if (typeof item !== "object" || item === null) return String(item);
      const { loc, msg } = item as { loc?: unknown; msg?: unknown };
      const where = Array.isArray(loc) ? loc.slice(1).join(".") : "";
      const what = typeof msg === "string" ? msg : JSON.stringify(item);
      return where ? `${where}: ${what}` : what;
    });
    return parts.join("; ");
  }

  /* And a plain object, which is the third shape a raised `HTTPException` can carry: the batch
   * routes report what was already spent on the failure path — `POST /drafts/variants` raises
   * 502 with `{"error": ..., "llm_calls": n, "image_calls": n}` because the drafts roll back
   * with the request and the money does not. Without this branch that landed as "request failed
   * (502)": the reason dropped, and the spend the backend went out of its way to report dropped
   * with it — on the one path where nothing arrived to show for it.
   *
   * The message first, then the remaining scalars as `key: value`. Never `String(detail)` or a
   * bare `JSON.stringify`: `[object Object]` on screen is the bug the branch above exists to
   * prevent, and this one must not reintroduce it in a different shape. After the array check,
   * because FastAPI's own 422s are a list of objects and keep their own handling. */
  if (typeof detail === "object" && detail !== null) {
    const entries = Object.entries(detail as Record<string, unknown>);
    const said = entries.find(
      ([k, v]) => typeof v === "string" && v && ["error", "detail", "message"].includes(k),
    );
    const rest = entries
      .filter(([k, v]) => k !== said?.[0] && (typeof v === "string" || typeof v === "number"))
      .map(([k, v]) => `${k}: ${v}`);
    const parts = [said?.[1] as string | undefined, ...rest].filter(Boolean);
    if (parts.length > 0) return parts.join(", ");
  }

  return `request failed (${status})`;
}

/** One place where fetch is allowed to throw and a non-2xx is turned into a value.
 *
 *  `read` runs only on success, so a JSON endpoint and the PNG-returning preview endpoint
 *  share the same failure handling — the preview answers with an image on success and a
 *  JSON `detail` on failure, and that asymmetry lives here rather than in a component. */
async function request<T>(
  path: string,
  init: RequestInit,
  read: (res: Response) => Promise<T>,
): Promise<ApiResult<T>> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, init);
  } catch (e) {
    return {
      ok: false,
      kind: "network",
      message: e instanceof Error ? e.message : "could not reach the API",
    };
  }

  if (!res.ok) {
    // The error body is not guaranteed to be JSON — a proxy can answer 502 with HTML, and
    // some 500s carry no body at all. Parsed defensively *inside* the http branch: letting
    // this throw would land in the network branch above and report a live 500 as an
    // unreachable backend, which is the exact conflation this module exists to remove.
    const body = (await res.json().catch(() => null)) as { detail?: unknown } | null;
    return {
      ok: false,
      kind: "http",
      status: res.status,
      message: messageFrom(body?.detail, res.status),
      // The same value `messageFrom` was given, kept alongside what it made of it rather than
      // instead of it — see `ApiFailure`. Every existing call site reads `message` and is
      // untouched.
      detail: body?.detail,
    };
  }

  try {
    return { ok: true, data: await read(res) };
  } catch (e) {
    // A 200 whose body is not what was promised is a broken response, not an empty one.
    return {
      ok: false,
      kind: "http",
      status: res.status,
      message: e instanceof Error ? `malformed response: ${e.message}` : "malformed response",
    };
  }
}

/** A read. Safe to await directly in an async server component — it never throws. */
export function getJson<T>(path: string): Promise<ApiResult<T>> {
  return request<T>(path, { cache: "no-store" }, (res) => res.json() as Promise<T>);
}

/** A mutation. `method` covers the one PUT in the app; everything else posts. */
export function postJson<T>(
  path: string,
  body?: unknown,
  method: "POST" | "PUT" | "DELETE" = "POST",
): Promise<ApiResult<T>> {
  return request<T>(
    path,
    {
      method,
      headers: { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    },
    (res) => res.json() as Promise<T>,
  );
}

/** `postJson`'s twin for the one endpoint that takes bytes — `POST /assets` is multipart.
 *
 *  The header is the whole reason this exists rather than a flag on `postJson`: a hand-set
 *  `Content-Type: multipart/form-data` omits the boundary, which FastAPI cannot parse, so the
 *  upload would fail on every file. `fetch` derives the full header from the FormData when it
 *  is left alone. Same three outcomes as every other call — an upload that reports success on
 *  a 422 is how an asset library ends up missing the file someone just chose. */
export function postForm<T>(path: string, body: FormData): Promise<ApiResult<T>> {
  return request<T>(path, { method: "POST", body }, (res) => res.json() as Promise<T>);
}

/** ponytail: `postJson`'s twin for the one endpoint that answers with bytes —
 *  `POST /templates/{id}/preview` returns image/png. Three lines of duplication beats
 *  either a response-type parameter on postJson or leaving that call site hand-rolled. */
export function postBlob(path: string, body?: unknown): Promise<ApiResult<Blob>> {
  return request<Blob>(
    path,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    },
    (res) => res.blob(),
  );
}
