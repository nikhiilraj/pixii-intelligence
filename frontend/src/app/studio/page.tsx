import { ApiFailureNotice } from "@/components/api-failure";
import {
  API_BASE,
  getJson,
  type Asset,
  type Dossier,
  type Draft,
  type Health,
  type Publication,
  type PublishingTarget,
  type ResearchJobSummary,
  type Template,
} from "@/lib/api";

import Studio, { type DraftSummary } from "./Studio";

export const dynamic = "force-dynamic";

export default async function StudioPage({
  searchParams,
}: {
  searchParams: Promise<{ [key: string]: string | string[] | undefined }>;
}) {
  /* `?draft=<id>` — the whole of US-012's addressability, and it is validated here rather than
     handed to the API.
     ponytail: one regex, no parser. It is not distrust of the backend — `GET /drafts/abc`
     answers 422 with a usable message — it is that a query string has more shapes than a path
     segment does. `?draft=` is empty and would read as `/drafts/`, and `?draft=1&draft=2`
     arrives as an array; both would otherwise become a request nobody meant to make. */
  const params = await searchParams;
  const raw = params.draft;
  const wanted = (Array.isArray(raw) ? raw[0] : (raw ?? "")).trim();
  const id = /^\d+$/.test(wanted) ? wanted : null;

  /* `?research=<id>` — which research run to show under the draft, validated the same way and
     for the same reasons as `?draft=` above.

     **This is the whole of the draft→research link, and it is in the address bar because it is
     nowhere else.** `Draft` has no `research_job_id` column: nothing in the schema connects a
     draft to the research behind it, so there is no id to read off the draft. Resolving it here
     rather than inside the panel is what makes that a one-line change — the day the column
     exists, this becomes `requested.data.research_job_id` and the panel is untouched. */
  const rawResearch = params.research;
  const wantedResearch = (Array.isArray(rawResearch) ? rawResearch[0] : (rawResearch ?? "")).trim();
  const researchId = /^\d+$/.test(wantedResearch) ? wantedResearch : null;

  // The library is read here so the picker has something to offer. A failed read is handed on
  // as `null` rather than as `[]`: only the templates are load-bearing enough to replace the
  // whole page, and "the library is empty" is a claim a failed request cannot support. The
  // same holds for the drafts list, where it matters more — see `Drafts`.
  // `/health` for `variants_max` alone — the ceiling `POST /drafts/variants` clamps to, which
  // the variants control needs in order to say what a press will spend. In the `Promise.all`
  // rather than awaited after it: it is on the page's critical path and depends on nothing
  // here. A failed read is handed on as `null` and the control then says nothing about the
  // count; it never takes the page down, and it never falls back to 3.
  // The requested draft's command history rides alongside the draft itself rather than being
  // fetched by the panel after it mounts. A Publish button rendered before its history has
  // arrived is a Publish button rendered without the history, and `GET /publications`' own
  // docstring names that as how one post gets commanded twice. A failed read is handed on as
  // `null` and the panel says so — it never renders as "nothing has been commanded".
  // The dossier `?research=` named, and the index of runs that exist. Both in the `Promise.all`
  // rather than after it: neither depends on anything here, and a research panel that fills in
  // after the draft has painted is a panel a reviewer reads the page without.
  const [
    templates,
    assets,
    drafts,
    requested,
    health,
    publications,
    publishing,
    dossier,
    jobs,
  ] = await Promise.all([
    getJson<Template[]>("/templates"),
    getJson<Asset[]>("/assets"),
    getJson<Draft[]>("/drafts"),
    id === null ? Promise.resolve(null) : getJson<Draft>(`/drafts/${id}`),
    getJson<Health>("/health"),
    id === null ? Promise.resolve(null) : getJson<Publication[]>(`/drafts/${id}/publications`),
    // Where a command would go and whether it may go at all. Read unconditionally — it is one
    // small response and the panel needs both fields before the first button is pressed, not
    // after a command comes back 403.
    getJson<PublishingTarget>("/publishing"),
    researchId === null ? Promise.resolve(null) : getJson<Dossier>(`/research/${researchId}`),
    // The index is read whether or not a run was named, because it is what a reviewer looking
    // at an unresearched draft needs in order to reach one. A failed read is handed on as
    // `null` and the panel says so: "no runs exist" is a claim a failed request cannot support.
    getJson<ResearchJobSummary[]>("/research"),
  ]);

  /* Why the requested draft is not on screen, in words, or `null` when none was asked for.
     The API's own `detail` is surfaced rather than rewritten — `no draft 999` is already a
     sentence written for a person, and composing over it would only add a second voice. */
  const missing =
    wanted === ""
      ? null
      : id === null
        ? `"${wanted}" is not a draft id. A draft id is a number, as in /studio?draft=21.`
        : requested && !requested.ok
          ? requested.kind === "network"
            ? `Could not reach ${API_BASE} (${requested.message}).`
            : `HTTP ${requested.status}: ${requested.message}`
          : null;

  /* Why the requested research run is not on screen — the same three-way distinction `missing`
     makes for the draft, because "none was asked for", "that id is not a number" and "the read
     failed" are three different things to tell a reviewer and only one of them is an error. */
  const researchUnavailable =
    wantedResearch === ""
      ? null
      : researchId === null
        ? `"${wantedResearch}" is not a research job id. A job id is a number, as in ?research=4.`
        : dossier && !dossier.ok
          ? dossier.kind === "network"
            ? `Could not reach ${API_BASE} (${dossier.message}).`
            : `HTTP ${dossier.status}: ${dossier.message}`
          : null;

  /* Narrowed to three fields before it crosses into the client component. `GET /drafts` answers
     with every draft's full `DraftOut`, base64 PNG included — 530KB for the seven rows in the
     database today — and all of it would otherwise be serialized into the page just to render a
     list of links. */
  const summaries: DraftSummary[] | null = drafts.ok
    ? drafts.data.map((d) => ({ id: d.id, idea: d.idea, zernio_post_id: d.zernio_post_id }))
    : null;

  return (
    <main className="mx-auto max-w-6xl px-6 py-16">
      <p className="text-caption font-medium uppercase tracking-[0.18em] text-muted">Compose</p>
      <h1 className="mt-2 text-display font-semibold tracking-[-0.04em]">Studio</h1>
      <p className="mt-2 max-w-2xl text-body text-muted">
        Turn one clear point into a reviewable draft, with the exact templates and assets that
        produced it kept visible. Nothing publishes on its own — a person commands it, against
        the exact revision they read, and is asked to confirm first.
      </p>
      {templates.ok ? (
        /* Keyed by the requested id so a link from one draft to another remounts the component.
           Studio holds the draft it is showing in state, seeded from `initialDraft`; a
           client-side navigation to a different `?draft=` re-runs this server component but
           reconciles onto the same client instance, which would keep showing the previous
           draft. A `key` is React's answer to that. An effect is not — React 19 forbids syncing
           derived state through one and the compiler is enabled, which is the same reason
           `chooseVisual` is a handler. */
        <Studio
          key={wanted}
          templates={templates.data}
          assets={assets.ok ? assets.data : null}
          drafts={summaries}
          initialDraft={requested?.ok ? requested.data : null}
          missing={missing}
          variantsMax={health.ok ? health.data.variants_max : null}
          publications={publications?.ok ? publications.data : null}
          /* `null` when the read failed, which is deliberately not the same as either field's
             own falsy value. An unread flag is not "publishing is off" and an unread account is
             not "there is no account" — the panel says which, and only a *read* `false`
             disables anything. */
          publishing={publishing.ok ? publishing.data : null}
          research={{
            dossier: dossier?.ok ? dossier.data : null,
            unavailable: researchUnavailable,
            // `null` when the index read failed, which is not "no research has been run" — the
            // same distinction the assets and drafts reads above carry, and it matters more
            // here: a panel saying nothing has been researched, on a failed request, is the
            // input that gets an uncited draft pushed.
            jobs: jobs.ok ? jobs.data : null,
          }}
        />
      ) : (
        <ApiFailureNotice failure={templates} className="mt-8" />
      )}
    </main>
  );
}
