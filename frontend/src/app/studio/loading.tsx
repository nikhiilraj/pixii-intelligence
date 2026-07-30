import { Skeleton } from "@/components/ui/skeleton";

/* Mirrors studio/page.tsx + Studio: title, lede, then the two-column grid — the idea textarea,
   the three template selects and the two generate buttons on the left; the draft column on the
   right. The `22rem` left column is reserved at its real width so the form does not resize once
   the templates arrive.

   The right column is deliberately just a heading-height bar and nothing else. There is no draft
   on arrival — Studio holds one in session state and starts empty every time — so a skeleton
   promising a post here would be a promise about the wrong thing entirely. */
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
        </section>

        <section>
          <Skeleton className="h-4 w-40" />
        </section>
      </div>
    </main>
  );
}
