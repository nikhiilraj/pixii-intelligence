import Link from "next/link";

import { Skeleton } from "@/components/ui/skeleton";

/* Mirrors posts/[id]/page.tsx: the back link stays a real link, because it is the one thing on
   this page that works before the request answers and the one thing wanted if it never does.
   Then the header line, the six metric tiles at their real grid, and the post body.

   No media block. 45 of 107 posts carry no `local_media_path`, so reserving space for a picture
   would be a guess that shifts the layout more often than it steadies it. */
const BODY_LINES = ["w-full", "w-full", "w-11/12", "w-full", "w-2/3"];

export default function PostDetailLoading() {
  return (
    <main className="mx-auto max-w-6xl px-6 py-16" aria-busy="true">
      <span role="status" className="sr-only">
        Loading this post…
      </span>

      <Link href="/posts" className="text-meta text-muted hover:underline">
        ← Corpus
      </Link>

      <header className="mt-6 border-b border-border pb-6">
        <Skeleton className="h-3 w-28" />
        <div className="mt-2 flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <Skeleton className="h-7 w-40" />
        <Skeleton className="h-4 w-48" />
        </div>
      </header>

      <section className="mt-8 grid grid-cols-2 gap-3 sm:grid-cols-3">
        {[0, 1, 2, 3, 4, 5].map((tile) => (
          <div key={tile} className="rounded-card border border-border p-3">
            <Skeleton className="h-3 w-24" />
            <Skeleton className="mt-2 h-6 w-16" />
          </div>
        ))}
      </section>

      <div className="mt-10 grid gap-10 lg:grid-cols-[minmax(0,1fr)_25.75rem]">
      <div className="space-y-2">
        {BODY_LINES.map((width, i) => (
          <Skeleton key={i} className={`h-4 ${width}`} />
        ))}
      </div>
      <aside className="border-t-2 border-ink pt-4">
        <Skeleton className="h-3 w-24" />
        <Skeleton className="mt-3 h-6 w-64" />
        <Skeleton className="mt-3 h-20 w-full" />
      </aside>
      </div>
    </main>
  );
}
