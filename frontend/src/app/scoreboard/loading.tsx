import Link from "next/link";

import { Skeleton } from "@/components/ui/skeleton";

export default function ScoreboardLoading() {
  return (
    <main className="mx-auto max-w-6xl px-6 py-16" aria-busy="true">
      <span role="status" className="sr-only">Loading the scoreboard…</span>
      <Link href="/templates" className="text-meta text-muted hover:underline">← Templates</Link>
      <div className="mt-6 border-b border-border pb-8">
        <Skeleton className="h-3 w-28" />
        <Skeleton className="mt-3 h-10 w-64" />
        <div className="mt-3 max-w-2xl space-y-2">
          <Skeleton className="h-4 w-full" />
          <Skeleton className="h-4 w-4/5" />
        </div>
      </div>

      <section className="mt-8">
        <Skeleton className="h-5 w-52" />
        <div className="mt-4 divide-y divide-border border-y border-border">
          {[0, 1, 2].map((row) => (
            <div key={row} className="grid gap-3 py-4 sm:grid-cols-[minmax(0,1fr)_14rem] sm:items-center">
              <div><Skeleton className="h-4 w-56" /><Skeleton className="mt-2 h-3 w-28" /></div>
              <Skeleton className="h-3 w-full" />
            </div>
          ))}
        </div>
      </section>

      <section className="mt-10 border-t border-border pt-6">
        <Skeleton className="h-5 w-48" />
        <Skeleton className="mt-3 h-4 w-3/4" />
      </section>
    </main>
  );
}
