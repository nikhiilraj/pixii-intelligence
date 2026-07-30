"use client";

import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

/** One post's readings over time. `GET /posts/{id}/history` had no reader before US-009.
 *
 *  Takes the plotted points already labelled rather than the raw snapshots, because the same
 *  labels appear in the caption the server renders beside this chart, and every export of a
 *  `"use client"` module is a client *reference* when a server component imports it — a
 *  formatter shared that direction cannot be called. One function in page.tsx, both readers.
 *
 *  Only engaged actions are plotted. `impressions` is on the same rows and is `0` on every
 *  scraped one because Zernio never measured it — plotting that would draw a line along the
 *  floor and present an absence as a measurement, which is the failure this app exists to
 *  avoid. Reach and likes are omitted for the plainer reason that one line answers the
 *  question the page asks.
 *
 *  ponytail: the `/posts` chart's recharts idiom copied, not refactored into a shared
 *  component — two call sites, different shapes and axes, and Explorer belongs to another
 *  change right now. Deliberately *not* copied: that page's `min-w-[46rem]` table wrapper.
 *  This page is `max-w-3xl` and a min-width here would push `document.scrollWidth` past a
 *  390px viewport. The ceiling is a third caller. */
export default function EngagementCurve({
  data,
}: {
  data: { at: string; engaged: number }[];
}) {
  return (
    <div className="mt-3 h-56 w-full">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 4, right: 8, bottom: 0, left: -18 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="currentColor" opacity={0.12} />
          <XAxis dataKey="at" tick={{ fontSize: 11 }} stroke="currentColor" opacity={0.5} />
          <YAxis tick={{ fontSize: 11 }} stroke="currentColor" opacity={0.5} />
          <Tooltip contentStyle={{ fontSize: 12, borderRadius: 8 }} labelStyle={{ fontSize: 12 }} />
          <Line
            type="monotone"
            dataKey="engaged"
            stroke="#F2610C"
            strokeWidth={2}
            dot={{ r: 2 }}
            name="engaged actions"
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
