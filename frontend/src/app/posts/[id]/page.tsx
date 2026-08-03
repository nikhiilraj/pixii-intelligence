import Link from "next/link";
import { notFound } from "next/navigation";

import { ApiFailureNotice } from "@/components/api-failure";
import {
  API_BASE,
  getJson,
  type Draft,
  type LineageEntry,
  type MetricSnapshot,
  type Post,
} from "@/lib/api";

import EngagementCurve from "./EngagementCurve";
import ExcludeToggle from "./ExcludeToggle";
import RetopicForm from "./RetopicForm";
import VerdictForm from "./VerdictForm";

export const dynamic = "force-dynamic";

const nf = new Intl.NumberFormat("en-US");

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-black/10 p-3 dark:border-white/15">
      <div className="text-xs uppercase tracking-wide text-muted">{label}</div>
      <div className="mt-1 text-lg font-semibold tabular-nums">{value}</div>
    </div>
  );
}

function isVideo(path: string): boolean {
  return /\.(mp4|mov|webm)$/i.test(path);
}

/** A reading carries a date *and* a time: every snapshot in the database today was written by
 *  syncs on 28 Jul 2026, two of them a second apart, so a date-only label would print two
 *  distinct points identically. The year is there for the reason `Explorer`'s `shortDate` has
 *  one — the corpus spans 2024→2026. */
function readingLabel(value: string): string {
  return new Date(value).toLocaleString("en-GB", {
    day: "2-digit",
    month: "short",
    year: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** One family, and the exact version of it this draft was generated from.
 *
 *  The version is rendered from `draft.lineage`, which `_lineage` in api_drafts.py resolved by
 *  `(family_id, version)`. It is never re-resolved here, and this page never asks
 *  `/templates` at all: resolving a family to its newest version is G2 finding #3, where a v2
 *  draft redrawn after v3 existed credited v2 with v3's work in both the Zernio metadata and
 *  `template_performance`. This block is an attribution record, and a wrong one is worse than
 *  none. */
function Lineage({ label, entry }: { label: string; entry: LineageEntry }) {
  return (
    <div className="rounded-lg border border-black/10 p-3 dark:border-white/15">
      <div className="text-xs uppercase tracking-wide text-muted">{label}</div>
      {entry ? (
        <>
          <div className="mt-1 break-words text-sm font-medium">{entry.name}</div>
          <div className="mt-0.5 text-xs tabular-nums text-muted">v{entry.version}</div>
        </>
      ) : (
        <div className="mt-1 text-sm text-muted">not recorded</div>
      )}
    </div>
  );
}

/** The curve, or the reason there isn't one. Never an empty chart.
 *
 *  Two readings taken a second apart by consecutive syncs are two readings, not a trend, and a
 *  flat segment with nothing said about it reads as "engagement stopped" rather than "measured
 *  twice, effectively once" — which is the state of all 50 posts that carry snapshots today.
 *  The count and the window are rendered here, in the server component, rather than inside the
 *  chart: they are the claim, and they have to be true whether or not recharts draws. */
function Readings({ snapshots }: { snapshots: MetricSnapshot[] }) {
  if (snapshots.length === 0) {
    return (
      <p className="mt-2 text-sm text-muted">
        No readings have been taken of this post yet, so there is no curve to draw.
      </p>
    );
  }

  const first = readingLabel(snapshots[0].captured_at);
  const last = readingLabel(snapshots[snapshots.length - 1].captured_at);

  if (snapshots.length === 1) {
    return <p className="mt-2 text-sm text-muted">One reading, {first}. A curve needs two.</p>;
  }

  const flat = snapshots.every((s) => s.engaged_actions === snapshots[0].engaged_actions);

  return (
    <>
      <p className="mt-2 text-sm text-muted">
        {snapshots.length} readings, {first} → {last}.
        {flat ? " Engaged actions unchanged across all of them." : ""}
      </p>
      <EngagementCurve
        data={snapshots.map((s) => ({ at: readingLabel(s.captured_at), engaged: s.engaged_actions }))}
      />
      <p className="mt-1 text-xs text-muted">
        Engaged actions only. Impressions sit on the same rows but are 0 wherever they were
        never measured, and a floor is not a reading.
      </p>
    </>
  );
}

export default async function PostDetail({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const result = await getJson<Post>(`/posts/${id}`);

  // Only an actual 404 is a missing post. Every other failure used to arrive here as `null`
  // too, so a 500 or a dead backend rendered "this page could not be found" — a wrong answer
  // that looks like a right one, and the reason a page needs the status rather than a null.
  if (!result.ok) {
    if (result.kind === "http" && result.status === 404) notFound();
    return (
      <main className="mx-auto max-w-3xl px-6 py-16">
        <Link href="/posts" className="text-sm text-muted hover:underline">
          ← Corpus
        </Link>
        <ApiFailureNotice failure={result} className="mt-6" />
      </main>
    );
  }

  const post = result.data;
  const mediaUrl = post.local_media_path ? `${API_BASE}/media/${post.local_media_path}` : null;

  /* The draft that produced this post, if this app produced it — `metrics.draft_for_post`'s
     join (`Draft.zernio_post_id == Post.late_post_id`, a different namespace from `zernio_id`)
     done in the database.
     **Three outcomes, not two, and the middle one is normal.** A `DraftOut`; a 200 with a
     `null` body for a post with no draft behind it, which is the answer for 57 of the 57
     published posts here; a 404 for no such post, which cannot arrive after the read above
     succeeded unless the row went away between the two. So `ok: true` with `data === null` is
     an answer and is rendered as one — treating it as a failure would put an error card on
     every post in the corpus.
     This replaced `GET /drafts?limit=500` plus a `find` here. That join had a silent ceiling:
     the list is `created_at DESC`, so what fell off the end was the *oldest* drafts — exactly
     the ones whose posts have been live longest and are most likely to be read on this page —
     and the page then said "no draft behind this post" while the draft sat in the database. */
  const draftResult = await getJson<Draft | null>(`/posts/${post.id}/draft`);
  const draft = draftResult.ok ? draftResult.data : null;
  /* Only fetched when there is a draft, because the curve is only shown when there is one:
     the two blocks are one attribution surface, so a post nobody generated gets neither. */
  const history = draft ? await getJson<MetricSnapshot[]>(`/posts/${post.id}/history`) : null;

  return (
    <main className="mx-auto max-w-3xl px-6 py-16">
      <Link href="/posts" className="text-sm text-muted hover:underline">
        ← Corpus
      </Link>

      <header className="mt-6 flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h1 className="text-xl font-semibold tracking-tight">
          {post.account_username ?? post.platform}
        </h1>
        <span className="text-sm text-muted">
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
            className="text-sm text-muted underline"
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

      {/* What generated this post, and how it did — the two routes that had no reader.
          Both are gated on the draft: with no lineage there is nothing to attribute a curve
          to, and 57 of the published posts here are in exactly that state. A failed read is
          reported rather than answered as "nothing generated this" — that is a claim, and an
          absent draft and an unreachable one are not the same fact. The route says which:
          `null` is the absence, a non-2xx is the unreachable. */}
      {!draftResult.ok ? (
        <ApiFailureNotice failure={draftResult} className="mt-8" />
      ) : draft ? (
        <section className="mt-8">
          <h2 className="text-xs uppercase tracking-wide text-muted">Generated from</h2>
          <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-3">
            <Lineage label="hook" entry={draft.lineage.hook} />
            <Lineage label="structure" entry={draft.lineage.structure} />
            <Lineage label="visual" entry={draft.lineage.visual} />
          </div>

          {/* US-014, and it sits inside the lineage branch rather than beside it: re-topic is
              the action the three versions above make possible, and `POST /drafts/retopic`
              answers 409 for a post with no draft behind it — 57 of the published posts here.
              An affordance that can only fail is worse than none, so it shares the gate.
              `post.id` is this row's own id and goes as `source_post_id`; `late_post_id` is
              the lineage join's key and `draft.id` is a different table. */}
          <RetopicForm postId={post.id} />

          <h2 className="mt-8 text-xs uppercase tracking-wide text-muted">Engagement over time</h2>
          {history && !history.ok ? (
            <ApiFailureNotice failure={history} className="mt-3" />
          ) : (
            <Readings snapshots={history?.data ?? []} />
          )}
        </section>
      ) : (
        <p className="mt-8 text-sm text-muted">
          No draft behind this post — it came from the corpus rather than from anything this app
          generated, so there is no lineage to attribute and no curve is drawn against one.
        </p>
      )}

      {/* The page the Inbox's fourth queue links to, and therefore the page that has to be able
          to clear it. */}
      <VerdictForm postId={post.id} verdict={post.verdict} note={post.verdict_note} />

      <ExcludeToggle postId={post.id} excluded={post.excluded_from_extraction} />
    </main>
  );
}
