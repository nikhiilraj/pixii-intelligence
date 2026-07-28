import Link from "next/link";
import { notFound } from "next/navigation";

import { API_BASE, getJson, type Post } from "@/lib/api";

export const dynamic = "force-dynamic";

const nf = new Intl.NumberFormat("en-US");

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-black/10 p-3 dark:border-white/15">
      <div className="text-xs uppercase tracking-wide opacity-50">{label}</div>
      <div className="mt-1 text-lg font-semibold tabular-nums">{value}</div>
    </div>
  );
}

function isVideo(path: string): boolean {
  return /\.(mp4|mov|webm)$/i.test(path);
}

export default async function PostDetail({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const post = await getJson<Post & { local_media_path: string | null }>(`/posts/${id}`);

  if (!post) notFound();

  const mediaUrl = post.local_media_path ? `${API_BASE}/media/${post.local_media_path}` : null;

  return (
    <main className="mx-auto max-w-3xl px-6 py-16">
      <Link href="/posts" className="text-sm opacity-60 hover:underline">
        ← Corpus
      </Link>

      <header className="mt-6 flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h1 className="text-xl font-semibold tracking-tight">
          {post.account_username ?? post.platform}
        </h1>
        <span className="text-sm opacity-60">
          {post.platform}
          {post.published_at
            ? ` · ${new Date(post.published_at).toLocaleDateString("en-GB", {
                day: "2-digit",
                month: "short",
                year: "numeric",
              })}`
            : ""}
          {post.is_external ? " · synced" : ""}
        </span>
        {post.platform_post_url && (
          <a
            href={post.platform_post_url}
            target="_blank"
            rel="noreferrer"
            className="text-sm underline opacity-60"
          >
            view on {post.platform}
          </a>
        )}
      </header>

      <section className="mt-8 grid grid-cols-2 gap-3 sm:grid-cols-3">
        <Metric label="Engaged actions" value={nf.format(post.engaged_actions)} />
        <Metric label="Impressions" value={nf.format(post.impressions)} />
        <Metric label="Reach" value={nf.format(post.reach)} />
        <Metric label="Likes" value={nf.format(post.likes)} />
        <Metric label="Comments" value={nf.format(post.comments)} />
        <Metric label="Engagement rate" value={post.engagement_rate.toFixed(2)} />
      </section>

      {mediaUrl && (
        <figure className="mt-8">
          {isVideo(post.local_media_path!) ? (
            <video src={mediaUrl} controls className="w-full rounded-lg" />
          ) : (
            /* Media is served by the backend at an arbitrary local path, not a
               configured next/image domain, so a plain img is correct here. */
            // eslint-disable-next-line @next/next/no-img-element
            <img
              src={mediaUrl}
              alt={`Media for post ${post.zernio_id}`}
              className="w-full rounded-lg"
            />
          )}
        </figure>
      )}

      <article className="mt-8 whitespace-pre-wrap text-[15px] leading-relaxed">
        {post.content}
      </article>
    </main>
  );
}
