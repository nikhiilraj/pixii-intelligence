import { Skeleton } from "@/components/ui/skeleton";

/* Mirrors operations/page.tsx: title, lede, then the four operation panels and the two
   read-only histories — the daily runs and the research runs. The container class list is
   retyped here rather than lifted out, and it has to match
   `page.tsx` exactly or the skeleton promises a different box than the one that arrives —
   `route-shells.test.tsx` compares the two.

   The panel bars are deliberately generic. Each real panel carries a heading, a credential
   badge row, a control and a button, and their heights differ; drawing four identical stacks
   is the honest promise — "four things are coming, in a border, in this order" — where
   reproducing each one's exact furniture would be a second copy of four components that
   change independently of this file. */
export default function OperationsLoading() {
  return (
    <main className="mx-auto max-w-6xl px-6 py-16" aria-busy="true">
      <span role="status" className="sr-only">
        Loading operations…
      </span>

      <Skeleton className="h-8 w-40" />
      <div className="mt-3 max-w-2xl space-y-2">
        <Skeleton className="h-4 w-full" />
        <Skeleton className="h-4 w-full" />
        <Skeleton className="h-4 w-2/3" />
      </div>

      <div className="mt-8 space-y-6">
        {[0, 1, 2, 3].map((panel) => (
          <div key={panel} className="space-y-3 rounded-card border border-border p-4">
            <Skeleton className="h-5 w-56" />
            <Skeleton className="h-4 w-full" />
            <div className="flex flex-wrap gap-2">
              <Skeleton className="h-5 w-28" />
              <Skeleton className="h-5 w-24" />
            </div>
            <div className="border-t border-border pt-3">
              <Skeleton className="h-8 w-40" />
            </div>
          </div>
        ))}

        {/* Two lists, drawn the same, because they are the same shape: a heading over bordered
            rows. Three rows apiece is a promise about the *form* and not about the count —
            both of these routes legitimately answer with an empty list, and a skeleton cannot
            know that in advance. Reserving rows that never arrive is a smaller lie than
            reserving nothing and letting the page grow by half a screen. */}
        {/* Whole class strings in the array, not a template literal built from a fragment:
            Tailwind reads this file as text and only emits utilities it can see written out. */}
        {[
          ["daily-runs", "h-5 w-44"],
          ["research", "h-5 w-36"],
        ].map(([list, heading]) => (
          <div key={list} className="space-y-3">
            <Skeleton className={heading} />
            <div className="divide-y divide-border rounded-card border border-border">
              {[0, 1, 2].map((row) => (
                <div key={row} className="space-y-2 px-3 py-2.5">
                  <Skeleton className="h-4 w-64" />
                  <Skeleton className="h-4 w-full" />
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
    </main>
  );
}
