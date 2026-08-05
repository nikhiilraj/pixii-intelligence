import { Card } from "@/components/ui/card";

/** What an operation actually did, counted.
 *
 *  **Every number here is a measured zero when it is zero, and prints as `0`.** This is the
 *  one place on this screen where the project's `—` rule does *not* apply, and getting it
 *  backwards is easy: `created: 0` from a corpus ingest means the run reached Zernio, read
 *  the posts and found nothing new — a measurement, and the most informative result the
 *  operation has. Writing `value || "—"` would hide a successful run behind an em dash that
 *  claims nobody looked. `measured()` in `studio/ResearchPanel.tsx` guards the same boundary
 *  from the other side, where the nulls are real.
 *
 *  So this component takes `number`, never `number | null`, and there is nowhere to pass an
 *  absence — the type is the guard. A route that one day reports an uncollected figure needs
 *  a different readout, not a nullable field here.
 *
 *  `note` is the sentence a bare count cannot carry: which pass the number came from, what it
 *  means that it is zero. */
export function Counts({
  rows,
  note,
}: {
  rows: { label: string; value: number; hint?: string }[];
  note?: string;
}) {
  return (
    <Card className="bg-surface-2 text-meta" role="status">
      <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1.5">
        {rows.map((row) => (
          <div key={row.label} className="contents">
            <dt className="text-muted">{row.label}</dt>
            <dd className="min-w-0 wrap-anywhere">
              <span className="font-mono font-medium tabular-nums">{row.value}</span>
              {row.hint && <span className="ml-2 text-caption text-muted">{row.hint}</span>}
            </dd>
          </div>
        ))}
      </dl>
      {note && <p className="mt-2 text-caption text-muted">{note}</p>}
    </Card>
  );
}
