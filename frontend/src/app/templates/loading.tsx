import { Skeleton } from "@/components/ui/skeleton";

/* Mirrors templates/page.tsx + TemplateManager: title, lede, then the two-column grid — the
   list of template versions on the left, the author/edit form on the right. The grid columns
   are reserved at their real widths (`1fr 20rem`) so the form does not slide sideways into
   place once the list arrives. */
export default function TemplatesLoading() {
  return (
    <main className="mx-auto max-w-6xl px-6 py-16" aria-busy="true">
      <span role="status" className="sr-only">
        Loading the template library…
      </span>

      <Skeleton className="h-8 w-40" />
      <div className="mt-3 max-w-2xl space-y-2">
        <Skeleton className="h-4 w-full" />
        <Skeleton className="h-4 w-3/4" />
      </div>

      <div className="mt-8 grid gap-10 lg:grid-cols-[1fr_20rem]">
        <section>
          {/* The cohort select and the two extraction buttons. */}
          <div className="mb-4 flex flex-wrap items-center gap-3">
            <Skeleton className="h-8 w-36" />
            <Skeleton className="h-8 w-56" />
            <Skeleton className="h-8 w-40" />
          </div>

          <ul className="space-y-2">
            {[0, 1, 2, 3, 4].map((row) => (
              <li key={row} className="rounded-card border border-border p-3">
                <div className="flex flex-wrap items-center gap-2">
                  <Skeleton className="h-4 w-48" />
                  <Skeleton className="h-3 w-20" />
                  <Skeleton className="h-5 w-20" />
                </div>
                <Skeleton className="mt-2 h-3 w-full max-w-lg" />
              </li>
            ))}
          </ul>
        </section>

        <section className="space-y-3">
          <Skeleton className="h-4 w-28" />
          <Skeleton className="h-9 w-full" />
          <Skeleton className="h-9 w-full" />
          <Skeleton className="h-40 w-full" />
          <Skeleton className="h-8 w-32" />
        </section>
      </div>
    </main>
  );
}
