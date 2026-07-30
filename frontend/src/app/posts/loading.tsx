import { Skeleton } from "@/components/ui/skeleton";

/* Mirrors posts/page.tsx + Explorer: title, lede, the six filter controls with their count,
   the engagement chart, then the table. The table header row carries its real headings — the
   columns are fixed, so a bar where "Engaged" belongs would hide structure that is already
   known — and the body is bars because the rows are what the request is for.

   The chart block reserves its 14rem so the table does not jump up the page when the series
   resolves. It is one bar, not a fake line: a drawn shape here would be an invented trend. */
export default function PostsLoading() {
  return (
    <main className="mx-auto max-w-6xl px-6 py-16" aria-busy="true">
      <span role="status" className="sr-only">
        Loading the corpus…
      </span>

      <Skeleton className="h-8 w-32" />
      <div className="mt-3 max-w-2xl space-y-2">
        <Skeleton className="h-4 w-full" />
        <Skeleton className="h-4 w-3/4" />
      </div>

      {/* The five filter controls, the direction toggle and the row count, at their real widths. */}
      <div className="mt-6 flex flex-wrap items-center gap-2">
        {["w-44", "w-32", "w-40", "w-36", "w-40"].map((width) => (
          <Skeleton key={width} className={`h-8 ${width}`} />
        ))}
        <Skeleton className="h-8 w-10" />
        <Skeleton className="h-4 w-16" />
      </div>

      <Skeleton className="mt-6 h-56 w-full" />

      <div className="mt-6 overflow-x-auto">
        <table className="w-full min-w-[46rem] border-collapse text-sm">
          <thead>
            <tr className="border-b border-border text-left text-muted">
              <th className="py-2 pr-4 font-medium">Post</th>
              <th className="py-2 pr-4 font-medium">Account</th>
              <th className="py-2 pr-4 font-medium">Channel</th>
              <th className="py-2 pr-4 font-medium">Published</th>
              <th className="py-2 pr-4 text-right font-medium">Engaged</th>
              <th className="py-2 pr-4 text-right font-medium">Impressions</th>
              <th className="py-2 text-right font-medium">ER</th>
            </tr>
          </thead>
          <tbody>
            {[0, 1, 2, 3, 4, 5, 6, 7].map((row) => (
              <tr key={row} className="border-b border-border last:border-0">
                <td className="py-3 pr-4">
                  <Skeleton className="h-4 w-full max-w-md" />
                </td>
                <td className="py-3 pr-4">
                  <Skeleton className="h-4 w-24" />
                </td>
                <td className="py-3 pr-4">
                  <Skeleton className="h-4 w-16" />
                </td>
                <td className="py-3 pr-4">
                  <Skeleton className="h-4 w-14" />
                </td>
                <td className="py-3 pr-4">
                  <Skeleton className="ml-auto h-4 w-10" />
                </td>
                <td className="py-3 pr-4">
                  <Skeleton className="ml-auto h-4 w-12" />
                </td>
                <td className="py-3">
                  <Skeleton className="ml-auto h-4 w-8" />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </main>
  );
}
