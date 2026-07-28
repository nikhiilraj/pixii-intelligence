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
        <h1 className="text-2xl font-semibold tracking-tight">Scoreboard</h1>
        <p className="mt-8 text-sm text-red-600 dark:text-red-400">
          Backend unreachable. Start it with{" "}
          <code className="rounded bg-black/5 px-1 dark:bg-white/10">make api</code>.
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
      <h1 className="text-2xl font-semibold tracking-tight">Scoreboard</h1>
      <p className="mt-1 max-w-2xl text-sm opacity-60">
        What each template version has actually done. Every figure carries its sample count,
        and anything under {threshold} posts is marked insufficient. Templates are not ranked
        against one another — at this sample size that would be reading noise.
      </p>

      {withEvidence.length === 0 && (
        <p className="mt-6 rounded-lg border border-amber-500/30 bg-amber-500/10 p-3 text-sm">
          No template has an attributed post yet. Analytics only covers published posts, so a
          draft pushed to Zernio contributes nothing until it is actually published.
        </p>
      )}

      {kinds.map((kind) => {
        const group = rows.filter((r) => r.kind === kind);
        if (group.length === 0) return null;
        return (
          <section key={kind} className="mt-8">
            <h2 className="text-xs font-medium uppercase tracking-widest opacity-50">{kind}</h2>
            <div className="mt-2 overflow-x-auto">
              <table className="w-full min-w-[42rem] border-collapse text-sm">
                <thead>
                  <tr className="border-b border-black/15 text-left dark:border-white/20">
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
                      className="border-b border-black/8 last:border-0 dark:border-white/10"
                    >
                      <td className="py-3 pr-4">
                        <Link
                          href={`/posts?template_family=${row.family}`}
                          className="hover:underline"
                        >
                          {row.name}
                        </Link>
                        <span className="ml-1.5 text-xs opacity-50">v{row.version}</span>
                      </td>
                      <td className="py-3 pr-4 text-xs opacity-60">{row.status}</td>
                      <td className="py-3 pr-4 text-right tabular-nums">{row.sample_count}</td>
                      <td className="py-3 pr-4 text-right tabular-nums">
                        {nf.format(row.total_engaged_actions)}
                      </td>
                      <td className="py-3 pr-4 text-right tabular-nums">
                        {row.sample_count ? row.mean_engaged_actions.toFixed(1) : "—"}
                      </td>
                      <td className="py-3 text-xs">
                        {row.sufficient ? (
                          <span className="text-emerald-700 dark:text-emerald-400">
                            enough to read
                          </span>
                        ) : (
                          <span className="text-amber-700 dark:text-amber-400">
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
