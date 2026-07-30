import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";

/* **Why the Inbox lives in an `(inbox)` route group.** This was `app/loading.tsx` first, and
   that was wrong in a way only a browser shows: a segment's `loading.tsx` is the Suspense
   fallback for that segment *and every route beneath it*, so the root one made a hard load of
   /posts, /studio, /assets and the rest render this four-gate Inbox skeleton first and then
   swap it for the route's own. Confirmed in the streamed HTML — on `GET /posts` the visible
   shell was "Loading the Inbox…" with the corpus skeleton sitting in a `<div hidden>` waiting
   to replace it. A route group is a segment that adds nothing to the URL, so `/` still serves
   this page and this fallback now belongs to it alone. Do not move it back up.

   Mirrors page.tsx: title, the lede, then four queue sections — heading, count, gate sentence,
   card of rows — and the status footer. A spinner would say "wait"; this says "four gates are
   coming", which is the only thing about this page worth knowing before it arrives.

   The headings are real words because the *structure* of this page is known before the request
   answers: there are four gates and they are always these four, in this order. What is not
   known is how many things are waiting at each, which is why every row is a bar.

   ponytail: the four titles are retyped here rather than lifted out of page.tsx into a shared
   array. A `loading.tsx` importing its own route's module to share a constant couples the
   skeleton to the page's module graph for four strings that have not changed since US-014.
   Ceiling: hoist them the day a fifth gate is added — `GET /inbox` returns a fixed four.

   Every `Skeleton` is `aria-hidden`, so without the `role="status"` line this page announces
   nothing at all while it loads. */
const GATES = [
  "Proposals awaiting review",
  "Built, awaiting push",
  "Pushed, awaiting Monte",
  "Published, awaiting verdict",
];

export default function InboxLoading() {
  return (
    <main className="mx-auto max-w-6xl px-6 py-16" aria-busy="true">
      <span role="status" className="sr-only">
        Loading the Inbox…
      </span>

      <Skeleton className="h-8 w-28" />
      <div className="mt-3 max-w-2xl space-y-2">
        <Skeleton className="h-4 w-full" />
        <Skeleton className="h-4 w-full" />
        <Skeleton className="h-4 w-2/3" />
      </div>

      <div className="mt-10 flex flex-col gap-10">
        {GATES.map((gate) => (
          <section key={gate}>
            <div className="flex items-baseline gap-2">
              <h2 className="text-head font-medium text-muted">{gate}</h2>
              <Skeleton className="h-4 w-5" />
            </div>
            <Skeleton className="mt-2 h-4 w-full max-w-xl" />
            <Card className="mt-3 px-2 py-1">
              {[0, 1].map((row) => (
                <div
                  key={row}
                  className="flex items-baseline justify-between gap-4 border-b border-border px-2 py-3 last:border-0"
                >
                  <Skeleton className="h-4 w-64" />
                  <Skeleton className="h-5 w-24 shrink-0" />
                </div>
              ))}
            </Card>
          </section>
        ))}
      </div>

      <footer className="mt-16 border-t border-border pt-6">
        <h2 className="text-caption font-medium uppercase tracking-widest text-muted">
          System status
        </h2>
        <div className="mt-2 flex flex-wrap gap-x-5 gap-y-2">
          {[0, 1, 2, 3].map((row) => (
            <Skeleton key={row} className="h-5 w-24" />
          ))}
        </div>
      </footer>
    </main>
  );
}
