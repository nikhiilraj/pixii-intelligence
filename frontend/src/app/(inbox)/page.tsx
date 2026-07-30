import Link from "next/link";

import { ApiFailureNotice } from "@/components/api-failure";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import {
  API_BASE,
  getJson,
  type Health,
  type Inbox,
  type InboxItem,
  type InboxQueue,
} from "@/lib/api";

export const dynamic = "force-dynamic";

/* The home page is the Inbox, and the Status page it replaces is gone — its rows survive as
   the footer below, which is information you can reach rather than a destination you visit.

   Why this page exists: the lineage circuit — propose a template, build a draft, push it,
   publish it, rule on it — has four gates that each need a human, and every one of them was
   invisible unless somebody remembered to go looking for it. A queue that nobody can see is
   indistinguishable from a queue that is empty.

   These are queues, not scores. No ordering between them means anything, they are not
   comparable with each other, and nothing here may be read as a template, draft or post having
   performed well or badly. That constraint is why there is no total, no chart and no sort
   control: every one of those would invite a comparison the data cannot support. */

/** "waiting 6 days" — the sentence the whole page is for.
 *
 *  0 and 1 are spelled out rather than formatted, because "waiting 0 days" and "waiting 1
 *  days" are both visible bugs on a page whose only number is an age. */
function waited(days: number): string {
  if (days <= 0) return "arrived today";
  if (days === 1) return "waiting 1 day";
  return `waiting ${days} days`;
}

/* ponytail: one invented threshold, no configuration. Nothing measured 7 days — it is a week,
   which is the point at which "in progress" stops being a plausible reading of a gate nobody
   has touched. It marks the GATE as stalled, never the item as bad: the same draft is neither
   better nor worse for having waited, and a tint that implied otherwise would be the scoring
   this page is built to avoid. Ceiling: a per-queue threshold, once any queue has ever been
   emptied and there is a real cadence to compare against. */
const STALLED_DAYS = 7;

function Item({ item, href }: { item: InboxItem; href: string }) {
  return (
    <li>
      <Link
        href={href}
        className="flex items-baseline justify-between gap-4 border-b border-border px-2 py-3 transition-colors last:border-0 hover:bg-surface-2"
      >
        <span className="text-body">{item.label}</span>
        {/* The age is a Badge, not a footnote: it is the loudest thing in the row after the
            label. Colour is on the fill, never the words — see badge.tsx. */}
        <Badge
          variant={item.age_days >= STALLED_DAYS ? "warning" : "neutral"}
          className="shrink-0 tabular-nums"
        >
          {waited(item.age_days)}
        </Badge>
      </Link>
    </li>
  );
}

/** One gate: what it holds, how long each thing has held there, and where a human goes next.
 *
 *  `empty` has to be true of every reason the queue can be empty, not just of the day the
 *  circuit has run zero laps — "nothing has ever been published" and "everything published has
 *  been ruled on" are the same empty list. So the copy states the state and what would fill it,
 *  and never claims a history it cannot see. */
function Queue({
  title,
  gate,
  queue,
  href,
  empty,
}: {
  title: string;
  gate: string;
  queue: InboxQueue;
  href: (item: InboxItem) => string;
  empty: string;
}) {
  return (
    <section>
      <div className="flex items-baseline gap-2">
        <h2 className="text-head font-medium">{title}</h2>
        <span className="font-mono text-meta tabular-nums text-muted">{queue.count}</span>
      </div>
      <p className="mt-0.5 max-w-2xl text-meta text-muted">{gate}</p>

      {queue.items.length === 0 ? (
        <Card className="mt-3 bg-surface-2 text-body text-muted">{empty}</Card>
      ) : (
        <Card className="mt-3 px-2 py-1">
          {/* Oldest first, exactly as the backend ordered it (`main._queue`). Not re-sorted
              here: the item that has waited longest is the one worth seeing, and any other
              order on this page would be a ranking. */}
          <ul>
            {queue.items.map((item) => (
              <Item key={item.id} item={item} href={href(item)} />
            ))}
          </ul>
        </Card>
      )}
    </section>
  );
}

function HealthRow({ label, ok }: { label: string; ok: boolean }) {
  return (
    <span className="flex items-center gap-1.5 text-meta">
      {label}
      <Badge variant={ok ? "success" : "danger"}>{ok ? "ok" : "down"}</Badge>
    </span>
  );
}

export default async function InboxPage() {
  // Two independent reads. The Inbox is the page; the health readout is a footer, so a failure
  // in one may not speak for the other — a dead /health must not blank the queues, and a
  // populated footer must not imply the queues loaded.
  const [inbox, health] = await Promise.all([
    getJson<Inbox>("/inbox"),
    getJson<Health>("/health"),
  ]);

  return (
    <main className="mx-auto max-w-5xl px-6 py-16">
      <h1 className="text-title font-semibold tracking-tight">Inbox</h1>
      <p className="mt-1 max-w-2xl text-body text-muted">
        Everything waiting on a human, and for how long — the four gates of the lineage circuit,
        in the order a post passes through them. These are queues, not scores: no ordering
        between them means anything, and nothing here says a template, draft or post performed
        well or badly.
      </p>

      {inbox.ok ? (
        <div className="mt-10 flex flex-col gap-10">
          <Queue
            title="Proposals awaiting review"
            gate="Extraction proposes; a human approves or retires. Only an approved template can be generated from."
            queue={inbox.data.proposals_awaiting_review}
            href={() => "/templates"}
            empty="No template is awaiting review. Extraction reads the corpus and proposes hooks, structures and visuals; each proposal waits here until someone approves or retires it."
          />
          <Queue
            title="Built, awaiting push"
            gate="Drafts written in Studio that have never reached Zernio."
            queue={inbox.data.built_awaiting_push}
            /* ponytail: `/studio`, not `/studio?draft=1`. Studio holds one in-session draft in
               local state and has no way to load an existing one, and teaching it to would be a
               second page's worth of work in a slice that owns this one. `GET /drafts/{id}`
               already exists, so the ceiling is a `?draft=` param read in studio/page.tsx. */
            href={() => "/studio"}
            empty="No draft is waiting to be pushed. A draft written in Studio waits here until it is pushed to Zernio — nothing in this app ever publishes on its own."
          />
          <Queue
            title="Pushed, awaiting Monte"
            gate="In Zernio as a draft, not yet live. Publishing is a human act performed there, not here — this app can only notice that it happened."
            queue={inbox.data.pushed_awaiting_monte}
            /* ponytail: this is the one gate with no in-app destination — the clearing action
               happens in Zernio's own dashboard. `InboxItem` carries no `zernio_post_id` and the
               only Zernio URL in this repo is the API root (`config.py:71`), so there is no
               honest external link to build; a synthesized dashboard URL would be a guess that
               looks like a fact. Studio is where the draft came from, so it is where the trail
               resumes. Ceiling: carry `zernio_post_id` on the item and link the real draft. */
            href={() => "/studio"}
            empty="No draft is waiting on a publish. A draft pushed to Zernio waits here until it goes live."
          />
          <Queue
            title="Published, awaiting verdict"
            gate="Live posts this app generated, with no ruling recorded. A verdict is one human judgement at n=1 — never a statistic."
            queue={inbox.data.published_awaiting_verdict}
            href={(item) => `/posts/${item.id}`}
            empty="Nothing is waiting on a verdict. A generated post that goes live lands here until someone rules on it."
          />
        </div>
      ) : (
        <ApiFailureNotice failure={inbox} className="mt-10" />
      )}

      {/* Where the Status page went. It stops being a destination — a health readout is what you
          check when something is wrong, not a home page — but the information stays reachable,
          and it stays on the page most likely to be open when the queues look wrong. */}
      <footer className="mt-16 border-t border-border pt-6">
        <h2 className="text-caption font-medium uppercase tracking-widest text-muted">
          System status
        </h2>
        {health.ok ? (
          <div className="mt-2 flex flex-wrap items-center gap-x-5 gap-y-2">
            <HealthRow label="API" ok={health.ok} />
            <HealthRow label="Database" ok={health.data.database} />
            {Object.entries(health.data.credentials).map(([name, present]) => (
              <HealthRow key={name} label={name} ok={present} />
            ))}
          </div>
        ) : (
          // Deliberately NOT ApiFailureNotice. Its network branch renders "Backend unreachable
          // — start it with `make api`", which underneath a fully populated Inbox is the same
          // conflation US-003 removed, inverted: the backend plainly answered. A footer failure
          // gets a footer-sized sentence that says what failed and nothing more.
          <p className="mt-2 text-meta text-muted">
            Status unavailable —{" "}
            {health.kind === "network"
              ? `could not reach ${API_BASE} (${health.message})`
              : `HTTP ${health.status}: ${health.message}`}
            .
          </p>
        )}
      </footer>
    </main>
  );
}
