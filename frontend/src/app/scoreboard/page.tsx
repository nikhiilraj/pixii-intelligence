import Link from "next/link";

import { ApiFailureNotice } from "@/components/api-failure";
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

function Samples({ count, threshold }: { count: number; threshold: number }) {
  const shown = Math.min(count, threshold);
  return (
    <div className="flex items-center gap-1.5" aria-label={`${count} published post${count === 1 ? "" : "s"}`}>
      {Array.from({ length: threshold }, (_, index) => (
        <span
          key={index}
          aria-hidden="true"
          className={`size-2 rounded-full border ${index < shown ? "border-text bg-text" : "border-absent"}`}
        />
      ))}
      <span className="ml-2 font-mono text-caption tabular-nums text-muted">n={count}</span>
    </div>
  );
}

export default async function ScoreboardPage() {
  const result = await getJson<Row[]>("/metrics/templates");

  if (!result.ok) {
    return (
      <main className="mx-auto max-w-6xl px-6 py-16">
        <p className="font-mono text-caption text-muted"><Link href="/templates" className="hover:underline">Templates</Link> · Scoreboard</p>
        <h1 className="mt-4 text-display font-semibold tracking-[-0.02em]">Scoreboard</h1>
        <ApiFailureNotice failure={result} className="mt-8" />
      </main>
    );
  }

  const rows = result.data;
  const threshold = rows[0]?.min_sample_size ?? 5;
  const observed = rows.filter((row) => row.sample_count > 0).sort((a, b) => a.name.localeCompare(b.name));
  const absent = rows.filter((row) => row.sample_count === 0).sort((a, b) => a.name.localeCompare(b.name));
  const readable = rows.filter((row) => row.sufficient);

  return (
    <main className="mx-auto max-w-6xl px-6 py-16">
      <p className="font-mono text-caption text-muted"><Link href="/templates" className="hover:underline">Templates</Link> · Scoreboard</p>
      <h1 className="mt-4 text-display font-semibold tracking-[-0.02em]">Scoreboard</h1>
      <p className="mt-3 max-w-2xl text-body text-muted">
        {readable.length === 0
          ? `Nothing here has enough posts behind it to read yet. ${observed.length} version${observed.length === 1 ? " has" : "s have"} one to four; ${absent.length} ${absent.length === 1 ? "has" : "have"} none.`
          : `${readable.length} version${readable.length === 1 ? " has" : "s have"} crossed the ${threshold}-post reading threshold.`}{" "}
        Each published post is one dot below—count them before reading anything else. Versions are alphabetical, never ranked.
      </p>

      {rows.length === 0 ? (
        <Card className="mt-8 border-dashed bg-surface-2 p-6 text-body">
          <p className="font-medium">No template version exists yet.</p>
          <p className="mt-2 text-muted">Extraction on <Link href="/templates" className="underline">Templates</Link> creates the library this page observes.</p>
        </Card>
      ) : (
        <>
          <section className="mt-12">
            <div className="flex items-end justify-between gap-4 border-b border-text pb-3">
              <div>
                <p className="font-mono text-caption uppercase tracking-[0.12em] text-muted">Observed</p>
                <h2 className="mt-1 text-head font-medium">{observed.length} version{observed.length === 1 ? "" : "s"} with posts</h2>
              </div>
              <p className="font-mono text-caption text-muted">alphabetical · not ranked · no averages</p>
            </div>

            {observed.length === 0 ? (
              <Card className="mt-4 border-dashed bg-surface-2 p-6 text-body">
                <p className="font-medium">No generated post has been published yet.</p>
                <p className="mt-2 text-muted">The scoreboard begins when the first draft completes the circuit. Zero here is measured waiting, not a broken analytics read.</p>
              </Card>
            ) : (
              <ol className="divide-y divide-border-subtle border-b border-border">
                {observed.map((row) => (
                  <li key={`${row.family}-${row.version}`} className="grid gap-3 py-5 md:grid-cols-[minmax(0,1fr)_20rem] md:items-center">
                    <div className="min-w-0">
                      <Link href={`/posts?template_family=${row.family}`} className="font-medium hover:underline">{row.name}</Link>
                      <span className="ml-2 font-mono text-caption text-muted">v{row.version}</span>
                      <p className="mt-1 font-mono text-caption text-muted">{row.kind} · {row.status}</p>
                    </div>
                    <Samples count={row.sample_count} threshold={threshold} />
                  </li>
                ))}
              </ol>
            )}
          </section>

          <section className="mt-12 border-t border-border pt-6">
            <div className="flex flex-wrap items-baseline justify-between gap-3">
              <h2 className="text-head font-medium text-muted">{absent.length} version{absent.length === 1 ? "" : "s"} with none</h2>
              <span className="font-mono text-caption text-muted">a measured zero, not a gap</span>
            </div>
            {absent.length > 0 && (
              <p className="mt-4 max-w-4xl text-body text-muted">
                {absent.map((row, index) => (
                  <span key={`${row.family}-${row.version}`}>
                    {index > 0 ? " · " : ""}<Link href={`/posts?template_family=${row.family}`} className="hover:text-text hover:underline">{row.name}</Link><span className="font-mono text-caption"> v{row.version}</span>
                  </span>
                ))}
              </p>
            )}
          </section>

          <p className="mt-10 max-w-2xl border-t border-text pt-6 text-meta text-muted">
            Five posts is where a figure starts being worth reading; the corpus spans 12.7×. This page withholds averages because arithmetic at n≈3 would look more certain than the evidence is.
          </p>
        </>
      )}
    </main>
  );
}
