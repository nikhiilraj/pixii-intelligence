import { ApiFailureNotice } from "@/components/api-failure";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import type { ApiResult, DailyRunSummary } from "@/lib/api";
/* From `lib/`, never from the `"use client"` panel that first defined it — this component is
   server-rendered and a client export is a reference that throws when called. See
   `lib/stamp.ts`; it is the reason `/operations` once served nothing but its skeleton. */
import { stamp } from "@/lib/stamp";

/** A count as the row recorded it. `null` is `—`, `0` is `0`, and the two are different facts.
 *
 *  Written as an explicit `=== null` and not as `count || "—"`, which is the same line with a
 *  bug in it: a run that finished and created no drafts would print `—`, saying nobody counted
 *  when somebody did — and it is exactly the run a person is scanning this list to find. The
 *  `ResearchSpend` comment in `lib/api.ts` names the same trap one screen over. */
function count(value: number | null): string {
  return value === null ? "—" : String(value);
}

/** What each recorded status means, in this screen's words rather than the column's.
 *
 *  `running` is deliberately **not** rendered as "in progress". A row sits on `running` for two
 *  reasons that the database cannot tell apart: a run that is genuinely working right now, and
 *  a process that died after claiming the day and has not yet been buried by a later tick —
 *  `daily.STALE_RUN_HOURS` is two hours, so the second can outlast the first by a long way.
 *  "In progress" would assert health nobody measured. "Claimed, unfinished" is what the row
 *  actually says.
 *
 *  **Not a quality order**, and nothing on this screen sorts by it. `complete` with zero drafts
 *  is a successful run that found nothing worth writing; it is not worse than one that made
 *  three. The badge distinguishes what happened to the run, not how good the run was. */
const STATUS: Record<string, { label: string; tone: "success" | "warning" | "danger" }> = {
  complete: { label: "complete", tone: "success" },
  failed: { label: "failed", tone: "danger" },
  running: { label: "claimed, unfinished", tone: "warning" },
};

/** Every daily editorial run this database has recorded, newest first.
 *
 *  The gap this closes: `DailyRun` rows were written from the day the daily slot became
 *  durable and nothing ever read them, so a run that failed every morning for a week looked
 *  from every screen exactly like a week in which nobody wrote anything. The counts, the error
 *  and the per-draft detail were on a Teams card and in a log line, and nowhere a person
 *  opens.
 *
 *  **What this list does not cover.** `DailyRun` rows are written by `daily.run_daily_slot`
 *  and by nothing else. The metrics sync and the reconciliation pass run on the same scheduler
 *  and record no run of their own — they update posts and publications and log a line — so
 *  their absence from this list is not evidence either way about whether they ran. The section
 *  says so on screen rather than letting a full list imply coverage it does not have.
 *
 *  **Read-only, and no action.** Asking whether the unattended work ran must not be a way to
 *  make it run; `POST /drafts/autonomous-run` behind `ConfirmedAction` is the deliberate
 *  control, and it is a different panel on this page. There is nothing here to confirm.
 *
 *  ponytail: no filter, no paging — the route's own default is thirty days, which is the
 *  window a person reads back over and more rows than this database has ever held. Ceiling: a
 *  date range the day somebody needs to answer a question about last quarter. */
export function DailyRunHistory({ runs }: { runs: ApiResult<DailyRunSummary[]> }) {
  return (
    <section className="space-y-4">
      <div>
        <h2 className="text-head font-medium">Daily editorial runs</h2>
        <p className="mt-1 max-w-2xl text-meta text-muted">
          Every run the scheduler recorded, newest first — when it claimed the day, what it
          produced, and why it stopped if it did. Newest first is a chronology, not a ranking.
          Only the daily editorial slot writes rows here: the metrics sync and the
          reconciliation pass leave log lines and nothing else, so this list says nothing about
          whether either of those ran.
        </p>
      </div>

      {/* Three outcomes, three renderings, and the middle one is the one worth getting right.
          An empty list is an honest answer — no run has ever been recorded — and rendering it
          as a failure would send someone to look for a broken scheduler. A read that never
          arrived is the opposite claim and must never borrow the empty copy. */}
      {!runs.ok ? (
        <ApiFailureNotice failure={runs} />
      ) : runs.data.length === 0 ? (
        <Card className="border-dashed text-meta">
          <p className="font-medium">No daily run has been recorded.</p>
          <p className="mt-1 text-muted">
            This is an honestly empty list, not a failure. A row appears the first time the
            scheduler claims a day, which needs{" "}
            <code className="font-mono">ENABLE_AUTONOMOUS</code> on and the slot hour to have
            passed. Until then there is nothing to report, and an empty list is the report.
          </p>
        </Card>
      ) : (
        <ul className="min-w-0 divide-y divide-border rounded-card border border-border">
          {runs.data.map((run) => {
            const status = STATUS[run.status] ?? { label: run.status, tone: "warning" as const };
            return (
              <li key={run.id} className="min-w-0 px-3 py-2.5" data-run={run.run_date}>
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-mono text-caption">{run.run_date}</span>
                  <Badge variant={status.tone}>{status.label}</Badge>
                  <span className="text-caption text-muted">
                    {run.slot} · claimed {stamp(run.started_at)} · finished{" "}
                    {stamp(run.finished_at)}
                  </span>
                </div>

                {/* Three counts, always all three, and `—` wherever the run never got far
                    enough to count. A row that shows drafts and hides the failures would read
                    as a clean run. */}
                <dl className="mt-1.5 flex flex-wrap gap-x-5 gap-y-1 text-meta">
                  <div className="flex gap-1.5">
                    <dt className="text-muted">Drafts created</dt>
                    <dd className="font-mono">{count(run.drafts_created)}</dd>
                  </div>
                  <div className="flex gap-1.5">
                    <dt className="text-muted">Topics failed</dt>
                    <dd className="font-mono">{count(run.topics_failed)}</dd>
                  </div>
                  {/* "Without a visual", not "visuals failed" — the same wording the Teams
                      card uses, and for the same reason: those drafts exist and their words
                      are intact, so it is a redraw and not a loss. */}
                  <div className="flex gap-1.5">
                    <dt className="text-muted">Drafts without a visual</dt>
                    <dd className="font-mono">{count(run.visuals_failed)}</dd>
                  </div>
                </dl>

                {run.status === "running" && (
                  <p className="mt-1.5 text-caption text-muted">
                    Claimed but not finished. Either it is still working or the process died
                    holding the day; the row cannot tell the two apart, and a later tick marks
                    it failed once it has been unfinished for too long.
                  </p>
                )}

                {/* No `role="alert"`. This is a recorded past failure, not a live one, and a
                    list of thirty of them would announce thirty alerts on load — the failed
                    badge above already carries the status into the reading order. */}
                {run.error && (
                  <p className="mt-1.5 wrap-anywhere text-meta text-text">
                    <span className="text-muted">Error </span>
                    {run.error}
                  </p>
                )}

                {/* `whitespace-pre-line`, because `detail` is one line per draft joined with
                    newlines. Collapsed into a paragraph the three failures a run named read as
                    one run-on sentence, which is how a fixable `UnresolvableAsset` gets lost
                    among the rest. */}
                {run.detail && (
                  <p className="mt-1.5 whitespace-pre-line wrap-anywhere text-caption text-muted">
                    {run.detail}
                  </p>
                )}

                {/* Delivery, reported separately from the run. An undelivered card is not a
                    failed run — `notify_run` writes this column only after Teams accepts,
                    precisely so a later tick can try again — so it is not badged and does not
                    colour the row.
                    **"The next tick retries it" would be the wrong words**, and the condition
                    is one line of `run_daily_slot`: the retry path returns early on
                    `not settings.teams_webhook_url`. With no webhook configured this column
                    stays NULL forever and nothing is pending, so a sentence promising a retry
                    would be a claim about a configuration the screen cannot see. Named
                    instead. */}
                <p className="mt-1.5 text-caption text-muted">
                  {run.notified_at
                    ? `Notification delivered ${stamp(run.notified_at)}`
                    : "No notification recorded — retried on a later tick, but only while a Teams webhook is configured."}
                </p>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
