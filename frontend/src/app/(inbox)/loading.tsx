import { Skeleton } from "@/components/ui/skeleton";

export default function InboxLoading() {
  return (
    <>
      <section aria-labelledby="loading-circuit-title" className="border-y border-border bg-surface">
        <h2 id="loading-circuit-title" className="sr-only">
          Lineage circuit
        </h2>
        <div className="mx-auto grid h-[170px] max-w-6xl grid-cols-4 items-center gap-12 px-6">
          {[0, 1, 2, 3].map((gate) => (
            <div key={gate} className="flex flex-col items-center gap-2">
              <Skeleton className="h-8 w-10" />
              <Skeleton className="h-3 w-24" />
              <Skeleton className="h-3 w-16" />
            </div>
          ))}
        </div>
      </section>

      <main className="mx-auto max-w-6xl px-6 pb-16 pt-12" aria-busy="true">
        <span role="status" className="sr-only">
          Loading the Inbox…
        </span>

        <Skeleton className="h-3 w-24" />
        <Skeleton className="mt-3 h-10 w-full max-w-xl" />
        <div className="mt-3 max-w-2xl space-y-2">
          <Skeleton className="h-4 w-full" />
          <Skeleton className="h-4 w-4/5" />
        </div>

        <section className="mt-12" aria-labelledby="loading-worklist-title">
          <div className="border-b border-text pb-3">
            <Skeleton className="h-3 w-20" />
            <h2 id="loading-worklist-title" className="sr-only">
              Work waiting on a person
            </h2>
            <Skeleton className="mt-2 h-6 w-52" />
          </div>
          <div className="overflow-hidden rounded-card border border-border">
            <Skeleton className="h-10 w-full rounded-none" />
            {[0, 1, 2, 3, 4].map((row) => (
              <div key={row} className="grid grid-cols-[104px_1fr_210px_100px] gap-4 border-t border-border-subtle px-5 py-4">
                <Skeleton className="h-4 w-20" />
                <Skeleton className="h-4 w-full max-w-sm" />
                <Skeleton className="h-4 w-36" />
                <Skeleton className="h-4 w-16" />
              </div>
            ))}
          </div>
        </section>

        <footer className="mt-16 border-t border-border pt-6">
          <Skeleton className="h-3 w-28" />
          <div className="mt-3 flex gap-5">
            {[0, 1, 2, 3].map((row) => (
              <Skeleton key={row} className="h-5 w-24" />
            ))}
          </div>
        </footer>
      </main>
    </>
  );
}
