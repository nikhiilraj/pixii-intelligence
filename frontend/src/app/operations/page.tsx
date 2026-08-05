import { ApiFailureNotice } from "@/components/api-failure";
import { Card } from "@/components/ui/card";
import { getJson, type Dossier, type Health, type ResearchJobSummary } from "@/lib/api";

import AutonomousRunPanel from "./AutonomousRunPanel";
import LinkedInImportPanel from "./LinkedInImportPanel";
import { ResearchHistory } from "./ResearchHistory";
import { CorpusIngestPanel, MetricsSyncPanel } from "./ZernioPanels";

export const dynamic = "force-dynamic";

/** The eighth screen: the work that is not writing a post.
 *
 *  Everything here was reachable only by `curl` before — the README's own end-to-end walk told
 *  you to run `curl -X POST http://localhost:8000/metrics/sync` in step 6, in the middle of an
 *  otherwise-clickable circuit. Four operations that cost money or reach outside, plus the
 *  research runs those operations and Studio produce.
 *
 *  **A screen, not a control panel.** Nothing here schedules anything, and nothing here can
 *  reach a publish command: `POST /drafts/autonomous-run` produces drafts and cannot push,
 *  which is a property of `run_autonomous` rather than of this page, and there is deliberately
 *  no control that would compose one. ADR 0002 is unaffected by this file.
 *
 *  Why its own route rather than a tab on the Inbox: the Inbox is a list of things waiting on a
 *  person and is the one screen with a promise about its contents. A button that spends money
 *  is not a queue item, and putting one among them would make "these are waiting on you" mean
 *  two different things.
 */
export default async function OperationsPage({
  searchParams,
}: {
  searchParams: Promise<{ [key: string]: string | string[] | undefined }>;
}) {
  const params = await searchParams;
  const typed = typeof params.job === "string" ? params.job : "";
  const jobId = /^\d+$/.test(typed) ? Number(typed) : null;

  // `/health` for the credential flags and the autonomous ceiling; `/research` for the run
  // list. In one `Promise.all` because neither depends on the other and both are on the page's
  // critical path. A failed `/health` is carried as `null` rather than as "nothing is
  // configured" — see `ConfirmedAction`, where that distinction decides whether a control is
  // disabled.
  const [health, jobs, dossier] = await Promise.all([
    getJson<Health>("/health"),
    getJson<ResearchJobSummary[]>("/research"),
    jobId === null ? Promise.resolve(null) : getJson<Dossier>(`/research/${jobId}`),
  ]);

  const credentials = health.ok ? health.data.credentials : null;
  const ceiling = health.ok ? health.data.autonomous_max_drafts : null;

  return (
    /* `max-w-6xl px-6` — the container six of the other seven screens use, and the one
       `app-nav.tsx` centres the navigation in. A different width here is the "every page
       misaligned with the nav" defect, and `route-shells.test.tsx` compares this class list
       against `loading.tsx`'s. Emitted before anything branches on a read, so the shell is
       the same whether or not the API answered. */
    <main className="mx-auto max-w-6xl px-6 py-16">
      <p className="text-caption font-medium uppercase tracking-[0.18em] text-muted">
        Unattended work
      </p>
      <h1 className="mt-2 text-display font-semibold tracking-[-0.04em]">Operations</h1>
      <p className="mt-2 max-w-2xl text-body text-muted">
        The parts of the circuit that are not writing: pulling the corpus in, pulling engagement
        back, and generating a batch without being watched. Each one costs money or reaches
        outside this machine, so each one says what it needs, what it will spend and what it
        will do before it does it. None of them publishes, schedules or pushes anything.
      </p>

      {/* The `/health` read is reported once, here, rather than four times inside the panels.
          They each say what an unread configuration means for them; this says what happened. */}
      {!health.ok && (
        <ApiFailureNotice failure={health} className="mt-8" />
      )}

      <div className="mt-8 space-y-6">
        <section className="space-y-4">
          <div>
            <h2 className="text-head font-medium">Zernio</h2>
            <p className="mt-1 max-w-2xl text-meta text-muted">
              The two reads that keep the corpus and its numbers current. Both are safe to
              repeat; neither writes anything to the account.
            </p>
          </div>
          <CorpusIngestPanel credentials={credentials} />
          <MetricsSyncPanel credentials={credentials} />
        </section>

        <section className="space-y-4">
          <div>
            <h2 className="text-head font-medium">Corpus</h2>
            <p className="mt-1 max-w-2xl text-meta text-muted">
              Material Zernio does not carry. One post at a time is the form on{" "}
              <code className="font-mono">/posts</code>; this is the bulk path.
            </p>
          </div>
          <LinkedInImportPanel />
        </section>

        <section className="space-y-4">
          <div>
            <h2 className="text-head font-medium">Generation</h2>
            <p className="mt-1 max-w-2xl text-meta text-muted">
              The same batch the daily scheduled run produces, on demand. It writes drafts here
              and reaches Zernio not at all — an unattended run cannot push, and this page adds
              no way for it to learn.
            </p>
          </div>
          <AutonomousRunPanel credentials={credentials} ceiling={ceiling} />
        </section>

        <ResearchHistory jobs={jobs} dossier={dossier} selected={jobId} />

        {/* A typed `?job=` that is not a number never reaches the API, so it would otherwise
            render as "no run selected" — silently the same as not having asked. */}
        {typed !== "" && jobId === null && (
          <Card role="alert" className="border-danger/40 bg-danger/10 text-meta">
            <p className="font-medium">&quot;{typed}&quot; is not a research job id.</p>
            <p className="mt-1 text-muted">
              A job id is a number, as in <code className="font-mono">/operations?job=4</code>.
              Nothing was requested.
            </p>
          </Card>
        )}
      </div>
    </main>
  );
}
