import Link from "next/link";

import { getJson, type Post } from "@/lib/api";

export const dynamic = "force-dynamic";

const nf = new Intl.NumberFormat("en-US");

function firstLine(content: string): string {
  const line = content.split("\n").find((l) => l.trim().length > 0) ?? "";
  return line.length > 90 ? `${line.slice(0, 90)}…` : line;
}

function formatDate(value: string | null): string {
  if (!value) return "—";
  return new Date(value).toLocaleDateString("en-GB", {
    day: "2-digit",
    month: "short",
    year: "numeric",
  });
}

export default async function PostsPage() {
  const posts = await getJson<Post[]>("/posts");

  if (posts === null) {
    return (
      <main className="mx-auto max-w-5xl px-6 py-16">
        <h1 className="text-2xl font-semibold tracking-tight">Corpus</h1>
        <p className="mt-4 text-sm text-red-600 dark:text-red-400">
          Backend unreachable. Start it with{" "}
          <code className="rounded bg-black/5 px-1 dark:bg-white/10">make api</code>.
        </p>
      </main>
    );
  }

  return (
    <main className="mx-auto max-w-5xl px-6 py-16">
      <div className="flex items-baseline justify-between gap-4">
        <h1 className="text-2xl font-semibold tracking-tight">Corpus</h1>
        <p className="text-sm opacity-60">
          {posts.length} {posts.length === 1 ? "post" : "posts"}, ranked by engaged actions
        </p>
      </div>

      {posts.length === 0 ? (
        <p className="mt-8 text-sm opacity-60">
          Nothing ingested yet. Run{" "}
          <code className="rounded bg-black/5 px-1 dark:bg-white/10">
            curl -X POST localhost:8000/corpus/ingest
          </code>
          .
        </p>
      ) : (
        // Wide content scrolls inside its own container so the page body never does.
        <div className="mt-8 overflow-x-auto">
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
                <tr
                  key={post.id}
                  className="border-b border-black/8 last:border-0 dark:border-white/10"
                >
                  <td className="max-w-md py-3 pr-4">
                    <Link href={`/posts/${post.id}`} className="hover:underline">
                      {firstLine(post.content)}
                    </Link>
                  </td>
                  <td className="py-3 pr-4 opacity-70">{post.platform}</td>
                  <td className="py-3 pr-4 tabular-nums opacity-70">
                    {formatDate(post.published_at)}
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
        </div>
      )}
    </main>
  );
}
