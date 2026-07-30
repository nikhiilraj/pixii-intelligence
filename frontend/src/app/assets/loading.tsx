import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";

/* Mirrors assets/page.tsx + AssetLibrary: title, lede, the drop zone, the two filter controls
   with their count, then the grid. The drop zone keeps its real dashed border and its real
   words — it is an upload target, not a readout, so it does not depend on the request that is
   in flight and there is no reason to grey it out.

   The tiles use the same `auto-fill minmax(11rem, 1fr)` grid as the real one, so the columns
   land where they will land rather than reflowing when the images arrive. */
export default function AssetsLoading() {
  return (
    <main className="mx-auto max-w-6xl px-6 py-16" aria-busy="true">
      <span role="status" className="sr-only">
        Loading the asset library…
      </span>

      <Skeleton className="h-8 w-24" />
      <div className="mt-3 max-w-2xl space-y-2">
        <Skeleton className="h-4 w-full" />
        <Skeleton className="h-4 w-full" />
        <Skeleton className="h-4 w-1/2" />
      </div>

      <div className="mt-8 flex min-h-24 flex-col items-center justify-center gap-1 rounded-card border border-dashed border-border px-4 py-6 text-center">
        <span className="text-body font-medium text-muted">Drop an image here, or choose a file</span>
        <span className="text-caption text-muted">
          PNG, JPEG, GIF or WEBP. Anything over 1600px on the long edge is stored downscaled.
        </span>
      </div>

      <div className="mt-6 flex flex-wrap items-center gap-2">
        <Skeleton className="h-8 w-32" />
        <Skeleton className="h-8 w-36" />
        <Skeleton className="h-4 w-16" />
      </div>

      <ul className="mt-6 grid grid-cols-[repeat(auto-fill,minmax(11rem,1fr))] gap-4">
        {[0, 1, 2, 3, 4, 5].map((tile) => (
          <li key={tile}>
            <Card className="flex h-full flex-col gap-2">
              <Skeleton className="h-28 w-full" />
              <div className="flex items-start justify-between gap-2">
                <Skeleton className="h-4 w-20" />
                <Skeleton className="h-5 w-14" />
              </div>
              <Skeleton className="h-3 w-16" />
              <Skeleton className="mt-auto h-8 w-full" />
            </Card>
          </li>
        ))}
      </ul>
    </main>
  );
}
