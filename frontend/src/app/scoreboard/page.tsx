import Link from "next/link";

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

export default async function ScoreboardPage() {
  const rows = await getJson<Row[]>("/metrics/templates");

  if (rows === null) {
    return (
      <main className="mx-auto max-w-5xl px-6 py-16">
        <h1 className="text-title font-semibold tracking-tight">Scoreboard</h1>
        <p className="mt-8 text-body text-danger">
          Backend unreachable. Start it with{" "}
          <code className="rounded-input bg-surface-2 px-1 font-mono">make api</code>.
        </p>
      </main>
    );
  }

  const threshold = rows[0]?.min_sample_size ?? 5;
  const withEvidence = rows.filter((r) => r.sample_count > 0);
  // Grouped by kind, ordered by name — deliberately NOT by performance. Ranking these
  // against each other at this sample size would be presenting noise as a finding.
  const kinds = ["hook", "structure", "visual"];

  return (
    <main className="mx-auto max-w-5xl px-6 py-16">
      <h1 className="text-title font-semibold tracking-tight">Scoreboard</h1>
      <p className="mt-1 max-w-2xl text-body text-muted">
        What each template version has actually done. Every figure carries its sample count,
        and anything under {threshold} posts is marked insufficient. Templates are not ranked
        against one another — at this sample size that would be reading noise.
      </p>

      {withEvidence.length === 0 && (
        <p className="mt-6 rounded-card border border-warning/30 bg-warning/10 p-3 text-body">
          No template has an attributed post yet. Analytics only covers published posts, so a
          draft pushed to Zernio contributes nothing until it is actually published.
        </p>
      )}

      {kinds.map((kind) => {
        const group = rows.filter((r) => r.kind === kind);
        if (group.length === 0) return null;
        return (
          <section key={kind} className="mt-8">
            <h2 className="text-caption font-medium uppercase tracking-widest text-muted">{kind}</h2>
            <div className="mt-2 overflow-x-auto">
              <table className="w-full min-w-2xl border-collapse text-meta">
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
                        {row.sufficient ? (
                          <span className="text-success">
                            enough to read
                          </span>
                        ) : (
                          <span className="text-warning">
                            too thin ({row.sample_count}/{threshold})
                          </span>
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
