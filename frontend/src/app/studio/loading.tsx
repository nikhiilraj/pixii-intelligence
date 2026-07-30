import { Skeleton } from "@/components/ui/skeleton";

/* Mirrors studio/page.tsx + Studio: title, lede, then the two-column grid — the idea textarea,
   the three template selects, the two generate buttons and the drafts list on the left; the
   draft column on the right. The `22rem` left column is reserved at its real width so the form
   does not resize once the templates arrive.

   The right column is deliberately just a heading-height bar and nothing else, and since US-012
   that is a choice rather than a certainty: `/studio?draft=<id>` does arrive with a draft to
   show, but `loading.tsx` takes no props and cannot read the search params, so a skeleton shaped
   like a post would promise one on every visit to bare `/studio`, where there is still nothing.
   It under-promises on the one route rather than over-promising on the other. */
export default function StudioLoading() {
  return (
    <main className="mx-auto max-w-6xl px-6 py-16" aria-busy="true">
      <span role="status" className="sr-only">
        Loading Studio…
      </span>

      <Skeleton className="h-8 w-24" />
      <div className="mt-3 max-w-2xl space-y-2">
        <Skeleton className="h-4 w-full" />
        <Skeleton className="h-4 w-2/3" />
      </div>

      <div className="mt-8 grid gap-10 lg:grid-cols-[22rem_1fr]">
        <section className="space-y-3">
          {/* The idea textarea, then hook / structure / visual. */}
          <Skeleton className="h-24 w-full" />
          <Skeleton className="h-9 w-full" />
          <Skeleton className="h-9 w-full" />
          <Skeleton className="h-9 w-full" />
          <div className="flex flex-wrap gap-2">
            <Skeleton className="h-8 w-36" />
            <Skeleton className="h-8 w-32" />
          </div>
          {/* The drafts list. Reserved unconditionally, unlike the right column: there is
              always a list to show, even when what it will say is that there are none. */}
          <Skeleton className="h-32 w-full" />
        </section>

        <section>
          <Skeleton className="h-4 w-40" />
        </section>
      </div>
    </main>
  );
}
