import Link from "next/link";

import { ApiFailureNotice } from "@/components/api-failure";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import type { ApiResult, Dossier, ResearchJobSummary } from "@/lib/api";
/* From `lib/`, never from `../studio/PublishPanel` where it used to live: this component is
   server-rendered, and importing a function out of a `"use client"` module hands back a client
   reference that throws when called. `DossierPanel` below is fine — a server component may
   *render* a client component, it just may not call one of its functions. */
import { stamp } from "@/lib/stamp";

import { DossierPanel } from "../studio/ResearchPanel";

/** Which runs are worth looking at differently. A `failed` run is the one that died, and
 *  `GET /research`'s own docstring says why it carries `state` on every row: collapsing a
 *  failed run into the list would make the failure invisible in the one place a person goes
 *  looking for it.
 *
 *  **Not a quality order.** `completed` with five uncited claims is a success — the run found
 *  out that nothing supports them. The badge distinguishes *what happened to the run*, not how
 *  good its findings were, and nothing on this screen sorts by it. */
function stateTone(state: string): "success" | "warning" | "danger" | "neutral" {
  if (state === "failed") return "danger";
  if (state === "completed") return "success";
  return "warning";
}

/** Every research run, newest first, and one dossier when a run is selected.
 *
 *  **Newest first is a chronology, not a ranking**, and the list is rendered in exactly the
 *  order the route returns it. Sorting these by source count, by claim coverage or by anything
 *  else is the never-rank rule applied to evidence — `research.dossier()`'s docstring refuses
 *  the same thing one level down.
 *
 *  The selection travels in `?job=`, so a run is linkable and a screenshot of one carries the
 *  address that produced it. A server round trip per selection rather than client state: this
 *  is a read of a row that never changes once written, and the alternative is a fetch, a
 *  loading flag and a cache for a list somebody opens twice a week.
 *
 *  ponytail: no filter, no search, no pagination — `limit=100` newest runs, which is more than
 *  this database holds. Ceiling: a question filter the day the list is long enough to scroll
 *  past what you remember. */
export function ResearchHistory({
  jobs,
  dossier,
  selected,
}: {
  jobs: ApiResult<ResearchJobSummary[]>;
  /** The selected run, or `null` when none was asked for. An `ApiResult` rather than a
   *  `Dossier | null` because a run that could not be opened — a 404 for a job id typed into
   *  the address bar — must not render as "no run selected". */
  dossier: ApiResult<Dossier> | null;
  selected: number | null;
}) {
  return (
    <section className="space-y-4">
      <div>
        <h2 className="text-head font-medium">Research runs</h2>
        <p className="mt-1 max-w-2xl text-meta text-muted">
          Every run this system has made, newest first. A run belongs to the draft that asked
          for it, but browsing them without holding a draft is its own need: a run that failed,
          one that produced only unknowns, one whose queries came back empty are all things you
          look for from here. Nothing on this list is ranked, and the order is a chronology.
        </p>
      </div>

      {/* Three outcomes, three renderings. A failed read must never draw the empty-list copy
          below — "no research has ever run" is a strong claim to make on a request that did
          not arrive. */}
      {!jobs.ok ? (
        <ApiFailureNotice failure={jobs} />
      ) : jobs.data.length === 0 ? (
        <Card className="border-dashed text-meta">
          <p className="font-medium">No research run has been recorded.</p>
          <p className="mt-1 text-muted">
            This is an honestly empty list, not a failure. A run is created by a Studio
            generation whose brief selected <code className="font-mono">light</code> or{" "}
            <code className="font-mono">deep</code>; an opinion-only draft makes no web request
            and records nothing here.
          </p>
        </Card>
      ) : (
        <ul className="min-w-0 divide-y divide-border rounded-card border border-border">
          {jobs.data.map((job) => {
            const active = job.job_id === selected;
            return (
              <li key={job.job_id} className="min-w-0">
                <Link
                  href={active ? "/operations" : `/operations?job=${job.job_id}`}
                  aria-current={active ? "true" : undefined}
                  className={`block min-w-0 px-3 py-2.5 transition-colors hover:bg-surface-2 ${
                    active ? "bg-surface-2" : ""
                  }`}
                >
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-mono text-caption text-muted">#{job.job_id}</span>
                    <Badge variant={stateTone(job.state)}>{job.state}</Badge>
                    {/* Both modes, always. One field would make "the system asked for light
                        and the run did none" unanswerable afterwards — the reason the dossier
                        carries the pair. */}
                    <span className="text-caption text-muted">
                      mode {job.mode} · floor {job.recommended_mode} · {stamp(job.researched_at)}
                    </span>
                  </div>
                  <p className="mt-1 min-w-0 wrap-anywhere text-meta">{job.question}</p>
                </Link>
              </li>
            );
          })}
        </ul>
      )}

      {dossier !== null &&
        (dossier.ok ? (
          <DossierPanel dossier={dossier.data} />
        ) : (
          <Card role="alert" className="border-danger/40 bg-danger/10 text-meta">
            <p className="font-medium">That run could not be opened.</p>
            <p className="mt-1 wrap-anywhere text-muted">
              {dossier.message}
              {dossier.kind === "http" &&
                dossier.status === 404 &&
                " The list above is what exists; a job id can be typed into the address bar and need not be one of them."}
            </p>
          </Card>
        ))}
    </section>
  );
}
