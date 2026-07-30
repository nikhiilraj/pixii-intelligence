export const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

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
};

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
  zernio_post_id: string | null;
  lineage: { hook: LineageEntry; structure: LineageEntry; visual: LineageEntry };
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
};

/** `GET /health`. Read by the Inbox footer — the only place it is consumed. */
export type Health = {
  status: string;
  database: boolean;
  credentials: Record<string, boolean>;
};

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
  | { ok: false; kind: "http"; status: number; message: string }
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
    return { ok: false, kind: "http", status: res.status, message: messageFrom(body?.detail, res.status) };
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
