"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { API_BASE, type Post, type Template } from "@/lib/api";

const SORTS = [
  "engaged_actions",
  "impressions",
  "reach",
  "likes",
  "comments",
  "shares",
  "engagement_rate",
  "published_at",
] as const;

const nf = new Intl.NumberFormat("en-US");

function firstLine(content: string): string {
  const line = content.split("\n").find((l) => l.trim().length > 0) ?? "";
  return line.length > 80 ? `${line.slice(0, 80)}…` : line;
}

function shortDate(value: string | null): string {
  if (!value) return "—";
  return new Date(value).toLocaleDateString("en-GB", { day: "2-digit", month: "short" });
}

type Filters = {
  platform: string;
  family: string;
  since: string;
  sort: (typeof SORTS)[number];
  order: "desc" | "asc";
};

export default function Explorer({
  initial,
  templates,
}: {
  initial: Post[];
  templates: Template[];
}) {
  const [posts, setPosts] = useState(initial);
  const [filters, setFilters] = useState<Filters>({
    platform: "",
    family: "",
    since: "",
    sort: "engaged_actions",
    order: "desc",
  });
  const [loading, setLoading] = useState(false);

  const platforms = useMemo(
    () => Array.from(new Set(initial.map((p) => p.platform))).sort(),
    [initial],
  );

  // Filtering is a user action, so it refetches from the handler rather than an effect.
  async function apply(change: Partial<Filters>) {
    const next = { ...filters, ...change };
    setFilters(next);
    setLoading(true);
    const params = new URLSearchParams({ sort: next.sort, order: next.order });
    if (next.platform) params.set("platform", next.platform);
    if (next.family) params.set("template_family", next.family);
    if (next.since) params.set("since", next.since);
    try {
      const res = await fetch(`${API_BASE}/posts?${params}`, { cache: "no-store" });
      setPosts(res.ok ? ((await res.json()) as Post[]) : []);
    } catch {
      setPosts([]);
    } finally {
      setLoading(false);
    }
  }

  // Engagement over time, oldest first — the shape of the run, not a leaderboard.
  const series = useMemo(
    () =>
      [...posts]
        .filter((p) => p.published_at)
        .sort((a, b) => (a.published_at! < b.published_at! ? -1 : 1))
        .map((p) => ({
          date: shortDate(p.published_at),
          engaged: p.engaged_actions,
          impressions: p.impressions,
        })),
    [posts],
  );

  const field =
    "rounded-md border border-black/15 bg-transparent px-2 py-1.5 text-sm dark:border-white/20";

  return (
    <>
      <div className="mt-6 flex flex-wrap items-center gap-2">
        <select
          value={filters.platform}
          onChange={(e) => apply({ platform: e.target.value })}
          className={field}
        >
          <option value="">all channels</option>
          {platforms.map((p) => (
            <option key={p} value={p}>
              {p}
            </option>
          ))}
        </select>

        <select
          value={filters.family}
          onChange={(e) => apply({ family: e.target.value })}
          className={field}
        >
          <option value="">any template</option>
          {templates.map((t) => (
            <option key={t.id} value={t.family_id}>
              {t.kind}: {t.name}
            </option>
          ))}
        </select>

        <input
          type="date"
          value={filters.since}
          onChange={(e) => apply({ since: e.target.value })}
          className={field}
          aria-label="published since"
        />

        <select
          value={filters.sort}
          onChange={(e) => apply({ sort: e.target.value as (typeof SORTS)[number] })}
          className={field}
        >
          {SORTS.map((s) => (
            <option key={s} value={s}>
              sort: {s.replace(/_/g, " ")}
            </option>
          ))}
        </select>

        <button
          onClick={() => apply({ order: filters.order === "desc" ? "asc" : "desc" })}
          className={field}
          aria-label="toggle sort direction"
        >
          {filters.order === "desc" ? "↓" : "↑"}
        </button>

        <span className="text-sm opacity-60">
          {loading ? "…" : `${posts.length} post${posts.length === 1 ? "" : "s"}`}
        </span>
      </div>

      {series.length > 1 && (
        <div className="mt-6 h-56 w-full">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={series} margin={{ top: 4, right: 8, bottom: 0, left: -18 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="currentColor" opacity={0.12} />
              <XAxis dataKey="date" tick={{ fontSize: 11 }} stroke="currentColor" opacity={0.5} />
              <YAxis tick={{ fontSize: 11 }} stroke="currentColor" opacity={0.5} />
              <Tooltip
                contentStyle={{ fontSize: 12, borderRadius: 8 }}
                labelStyle={{ fontSize: 12 }}
              />
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
      )}

      {/* Wide content scrolls inside its own container so the page body never does. */}
      <div className="mt-6 overflow-x-auto">
        <table className="w-full min-w-[46rem] border-collapse text-sm">
          <thead>
            <tr className="border-b border-black/15 text-left dark:border-white/20">
              <th className="py-2 pr-4 font-medium">Post</th>
              <th className="py-2 pr-4 font-medium">Channel</th>
              <th className="py-2 pr-4 font-medium">Published</th>
              <th className="py-2 pr-4 text-right font-medium">Engaged</th>
              <th className="py-2 pr-4 text-right font-medium">Impressions</th>
              <th className="py-2 text-right font-medium">ER</th>
            </tr>
          </thead>
          <tbody>
            {posts.map((post) => (
              <tr key={post.id} className="border-b border-black/8 last:border-0 dark:border-white/10">
                <td className="max-w-md py-3 pr-4">
                  <Link href={`/posts/${post.id}`} className="hover:underline">
                    {firstLine(post.content)}
                  </Link>
                </td>
                <td className="py-3 pr-4 opacity-70">{post.platform}</td>
                <td className="py-3 pr-4 tabular-nums opacity-70">
                  {shortDate(post.published_at)}
                </td>
                <td className="py-3 pr-4 text-right font-medium tabular-nums">
                  {nf.format(post.engaged_actions)}
                </td>
                <td className="py-3 pr-4 text-right tabular-nums opacity-70">
                  {nf.format(post.impressions)}
                </td>
                <td className="py-3 text-right tabular-nums opacity-70">
                  {post.engagement_rate.toFixed(2)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {posts.length === 0 && !loading && (
          <p className="py-6 text-sm opacity-60">Nothing matches those filters.</p>
        )}
      </div>
    </>
  );
}
