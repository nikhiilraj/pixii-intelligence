import { Skeleton } from "@/components/ui/skeleton";

/* Mirrors scoreboard/page.tsx: title, lede, then one section per kind with its real uppercase
   heading and its real table headings. The three kinds are fixed — `hook`, `structure`,
   `visual`, the `TemplateKind` union — so the sections are structure, not content.

   Two rows per section, not ten: the real page shows one row per template *version*, and a
   skeleton that promises a long table where three rows arrive is worse than one that promises
   little. What is unknown here is the count.

   ponytail: the kinds are retyped from page.tsx for the same reason app/loading.tsx retypes the
   gate names. Ceiling: export them from `lib/api.ts` beside `TemplateKind` if a fourth appears. */
const KINDS = ["hook", "structure", "visual"];

export default function ScoreboardLoading() {
  return (
    <main className="mx-auto max-w-5xl px-6 py-16" aria-busy="true">
      <span role="status" className="sr-only">
        Loading the scoreboard…
      </span>

      <Skeleton className="h-8 w-40" />
      <div className="mt-3 max-w-2xl space-y-2">
        <Skeleton className="h-4 w-full" />
        <Skeleton className="h-4 w-full" />
        <Skeleton className="h-4 w-3/4" />
      </div>

      {KINDS.map((kind) => (
        <section key={kind} className="mt-8">
          <h2 className="text-caption font-medium uppercase tracking-widest text-muted">{kind}</h2>
          <div className="mt-2 overflow-x-auto">
            <table className="w-full min-w-2xl border-collapse text-meta">
              <thead>
                <tr className="border-b border-border text-left text-muted">
                  <th className="py-2 pr-4 font-medium">Template</th>
                  <th className="py-2 pr-4 font-medium">Status</th>
                  <th className="py-2 pr-4 text-right font-medium">Posts</th>
                  <th className="py-2 pr-4 text-right font-medium">Engaged</th>
                  <th className="py-2 pr-4 text-right font-medium">Mean</th>
                  <th className="py-2 font-medium">Evidence</th>
                </tr>
              </thead>
              <tbody>
                {[0, 1].map((row) => (
                  <tr key={row} className="border-b border-border last:border-0">
                    <td className="py-3 pr-4">
                      <Skeleton className="h-4 w-48" />
                    </td>
                    <td className="py-3 pr-4">
                      <Skeleton className="h-3 w-16" />
                    </td>
                    <td className="py-3 pr-4">
                      <Skeleton className="ml-auto h-4 w-6" />
                    </td>
                    <td className="py-3 pr-4">
                      <Skeleton className="ml-auto h-4 w-10" />
                    </td>
                    <td className="py-3 pr-4">
                      <Skeleton className="ml-auto h-4 w-10" />
                    </td>
                    <td className="py-3">
                      <Skeleton className="h-5 w-28" />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      ))}
    </main>
  );
}
