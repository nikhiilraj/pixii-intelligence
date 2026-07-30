import Link from "next/link";

import { ApiFailureNotice } from "@/components/api-failure";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { getJson } from "@/lib/api";

export const dynamic = "force-dynamic";

type Row = {
  family: string;
  version: number;
  kind: string;
  name: string;
  status: string;
  sample_count: number;
  total_engaged_actions: number;
  total_impressions: number;
  mean_engaged_actions: number;
  sufficient: boolean;
  min_sample_size: number;
};

const nf = new Intl.NumberFormat("en-US");

/* One column grid for all three tables. Auto layout sized each table to its own longest
   template name, so `Status` landed at x=645 under hook, x=666 under structure and x=505
   under visual — three tables of identical shape reading as unrelated. `table-fixed` plus
   these widths makes the grid a property of the page rather than of each group's content.

   The widths sum to 752px, which is also what each table falls back to when the viewport
   is narrower than that; the `overflow-x-auto` wrapper scrolls it. Above 752 the surplus
   is distributed proportionally — identically for all three, so the edges stay aligned.

   ponytail: retyped in loading.tsx rather than shared, same as KINDS is there. Ceiling: a
   `<ScoreboardCols />` in a shared module if a third caller appears. */
function ScoreboardCols() {
  return (
    <colgroup>
      <col className="w-80" />
      <col className="w-24" />
      <col className="w-16" />
      <col className="w-24" />
      <col className="w-16" />
      <col className="w-28" />
    </colgroup>
  );
}

export default async function ScoreboardPage() {
  const result = await getJson<Row[]>("/metrics/templates");

  if (!result.ok) {
    return (
      <main className="mx-auto max-w-6xl px-6 py-16">
        <h1 className="text-title font-semibold tracking-tight">Scoreboard</h1>
        <ApiFailureNotice failure={result} className="mt-8" />
      </main>
    );
  }

  const rows = result.data;
  const threshold = rows[0]?.min_sample_size ?? 5;
  const withEvidence = rows.filter((r) => r.sample_count > 0);
  // Grouped by kind, ordered by name — deliberately NOT by performance. Ranking these
  // against each other at this sample size would be presenting noise as a finding.
  const kinds = ["hook", "structure", "visual"];

  return (
    <main className="mx-auto max-w-6xl px-6 py-16">
      <h1 className="text-title font-semibold tracking-tight">Scoreboard</h1>
      <p className="mt-1 max-w-2xl text-body text-muted">
        What each template version has actually done. Every figure carries its sample count,
        and anything under {threshold} posts is marked insufficient. Templates are not ranked
        against one another — at this sample size that would be reading noise.
      </p>

      {/* Two different empties, and collapsing them would be a lie in one direction or the
          other. `rows.length === 0` means the template library itself is empty, so there is
          nothing to score. `withEvidence.length === 0` means the library is full and no post
          has ever been attributed to any of it — which is the real state of this app today
          (zero generated drafts have gone live) and is a statement about the circuit, not
          about the templates.

          The second case keeps every table below it. All-zero sample counts are worth
          rendering: they say *which* versions are waiting, and replacing them with an empty
          state would hide a stocked library behind the words "nothing here".

          Neutral, not the warning tint this carried before. A scoreboard with nothing on it is
          the expected state of a circuit that has not run a lap, not a fault. */}
      {rows.length === 0 ? (
        <Card className="mt-6 bg-surface-2 text-body">
          <p className="font-medium">No template version exists yet.</p>
          <p className="mt-1 text-muted">
            This page reads the template library, and the library is empty. Extraction on the{" "}
            <Link href="/templates" className="underline">
              Templates
            </Link>{" "}
            page proposes hooks, structures and visuals from the corpus; every version appears
            here the moment it exists, at a sample count of zero, and starts carrying evidence
            once a post generated from it goes live.
          </p>
        </Card>
      ) : (
        withEvidence.length === 0 && (
          <Card className="mt-6 bg-surface-2 text-body">
            <p className="font-medium">
              No generated post has been published yet — the scoreboard starts with the first
              circuit.
            </p>
            <p className="mt-1 text-muted">
              Every version below is listed at a sample count of zero, which is what waiting
              looks like rather than what failure looks like. Analytics covers published posts
              only, and publishing is a human act performed in Zernio — a draft pushed from
              Studio contributes nothing until Monte publishes it. The first published post
              fills the first row.
            </p>
          </Card>
        )
      )}

      {kinds.map((kind) => {
        const group = rows.filter((r) => r.kind === kind);
        if (group.length === 0) return null;
        return (
          <section key={kind} className="mt-8">
            <h2 className="text-caption font-medium uppercase tracking-widest text-muted">{kind}</h2>
            <div className="mt-2 overflow-x-auto">
              <table className="w-full min-w-2xl table-fixed border-collapse text-meta">
                <ScoreboardCols />
                <thead>
                  <tr className="border-b border-border text-left">
                    <th className="py-2 pr-4 font-medium">Template</th>
                    <th className="py-2 pr-4 font-medium">Status</th>
                    <th className="py-2 pr-4 text-right font-medium">Posts</th>
                    <th className="py-2 pr-4 text-right font-medium">Engaged</th>
                    <th className="py-2 pr-4 text-right font-medium">Mean</th>
                    <th className="py-2 font-medium">Evidence</th>
                  </tr>
                </thead>
                <tbody>
                  {group.map((row) => (
                    <tr
                      key={`${row.family}-${row.version}`}
                      className="border-b border-border last:border-0"
                    >
                      <td className="py-3 pr-4">
                        <Link
                          href={`/posts?template_family=${row.family}`}
                          className="hover:underline"
                        >
                          {row.name}
                        </Link>
                        <span className="ml-1.5 text-caption text-muted">v{row.version}</span>
                      </td>
                      <td className="py-3 pr-4 text-caption text-muted">{row.status}</td>
                      <td className="py-3 pr-4 text-right font-mono tabular-nums">{row.sample_count}</td>
                      <td className="py-3 pr-4 text-right font-mono tabular-nums">
                        {nf.format(row.total_engaged_actions)}
                      </td>
                      <td className="py-3 pr-4 text-right font-mono tabular-nums">
                        {row.sample_count ? row.mean_engaged_actions.toFixed(1) : "—"}
                      </td>
                      <td className="py-3 text-caption">
                        {/* Was `text-success` / `text-warning`. Both fail AA as text on
                            light (3.29:1 and 2.06:1) — the status colour belongs on the
                            fill, which is what Badge does. See badge.tsx. */}
                        {row.sufficient ? (
                          <Badge variant="success">enough to read</Badge>
                        ) : (
                          <Badge variant="warning">
                            too thin ({row.sample_count}/{threshold})
                          </Badge>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        );
      })}
    </main>
  );
}
