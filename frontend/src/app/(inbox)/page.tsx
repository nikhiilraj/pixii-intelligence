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
   performed well or badly. That constraint is why there is no chart and no sort control, and
   why the queues carry no total: every one of those would invite a comparison the data cannot
   support.

   The closed-circuit counter is the one number that survives that rule, and it is worth being
   precise about why. It totals nothing on this page — it is not the queues added up — and it
   ranks nothing against anything. It answers a question about the machine, not about the work:
   has a draft ever gone all the way round? Four empty queues cannot answer that. "Nothing has
   ever been published" and "everything published has been ruled on" render identically, and
   that is the ambiguity this counter exists to remove. */

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

/* How many items a queue shows before it says how many it is holding back.
 *
 * The page rendered all 50 proposals in one column and stood 3815px tall (2998 when the finding
 * was filed, at 37 proposals), so the queue that most often needs no action owned the fold and
 * the three that do need action sat below it. Six brings it to 1703 and puts all four gates on
 * one screen at 1440px.
 *
 * What is hidden is *stated*, never silently dropped — same rule as everywhere else here: an
 * absence must not read as a measurement, and a queue that quietly showed six of fifty would
 * be exactly that. `queue.count` is the backend's own total (`main._queue` sends every row and
 * sets `count = len(items)`), and it is what the heading already prints, so the two agree.
 *
 * ponytail: a slice and a sentence. No paging control and no per-queue link to "the rest" —
 * every item in a queue links to the page that clears that whole gate, so the hidden items are
 * one click away through any visible one. Ceiling: a collective destination per queue, if a
 * queue ever gets one that is not just the page its items already point at. */
const QUEUE_CAP = 6;

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
            {queue.items.slice(0, QUEUE_CAP).map((item) => (
              <Item key={item.id} item={item} href={href(item)} />
            ))}
          </ul>
          {queue.count > QUEUE_CAP && (
            <p className="border-t border-border px-2 py-3 text-meta text-muted">
              Showing the {QUEUE_CAP} that have waited longest, of {queue.count}. Opening any
              row above reaches the page that clears the rest.
            </p>
          )}
        </Card>
      )}
    </section>
  );
}

/** "Closed circuits: 0" — whether the loop has ever run, which no queue can say.
 *
 *  Rendered at zero, always. A counter that hid itself when it had nothing to report would
 *  leave the page exactly as ambiguous as it was before it existed, and a dash or an em-rule
 *  would read as "not measured" when the measurement is the flat, certain 0 below. So the
 *  copy carries the same three beats as the queues' empty states: what the number is, that 0
 *  means never-yet rather than nothing-right-now, and what would make it 1.
 *
 *  That third beat names the *publish*, not the verdict. Zero drafts have ever gone live, so
 *  "rule on a live post and this becomes 1" would send a reader looking for a post that does
 *  not exist — the queues' own rule, never claim a history you cannot see. The step actually
 *  missing is Monte's, and it stays the missing step even once a draft is live and unruled.
 *
 *  A Card rather than a bare heading because it is not a fifth gate — nothing waits behind it,
 *  and giving it a queue's shape would put it in a list of things a human is meant to clear. */
function Circuits({ closed }: { closed: number }) {
  return (
    <Card className="mt-8 bg-surface-2">
      <h2 className="text-head font-medium">
        Closed circuits: <span className="font-mono tabular-nums">{closed}</span>
      </h2>
      <p className="mt-1 max-w-2xl text-meta text-muted">
        {closed === 0
          ? "The circuit has never been round: no draft this app generated has been pushed, published and ruled on, not once. A pushed draft that Monte publishes and someone then rules on makes this 1."
          : "Drafts written here that were pushed to Zernio, published by a human, and then ruled on — one full lap each."}{" "}
        A count of laps, not a score: it says the circuit ran, never that a post did well.
      </p>
    </Card>
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
    <main className="mx-auto max-w-6xl px-6 py-16">
      <h1 className="text-title font-semibold tracking-tight">Inbox</h1>
      <p className="mt-1 max-w-2xl text-body text-muted">
        Everything waiting on a human, and for how long — the four gates of the lineage circuit,
        in the order a post passes through them. These are queues, not scores: no ordering
        between them means anything, and nothing here says a template, draft or post performed
        well or badly.
      </p>

      {inbox.ok ? (
        <>
          {/* Inside `inbox.ok`, with the queues: a lap count invented out of a failed read
              would be the page making its strongest claim about the circuit on no data. */}
          <Circuits closed={inbox.data.closed_circuits} />
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
              /* The item id IS the draft id — `InboxItem` carries the id of whatever the gate
                 acts on, which for this queue is the row `/drafts/{id}/push` takes. So the link
                 opens that draft in Studio, where the push button is. It pointed at a bare
                 `/studio` until US-012, which was a link to a page that was empty on every
                 visit: Studio had no way to load an existing draft. */
              href={(item) => `/studio?draft=${item.id}`}
              empty="No draft is waiting to be pushed. A draft written in Studio waits here until it is pushed to Zernio — nothing in this app ever publishes on its own."
            />
            <Queue
              title="Pushed, awaiting Monte"
              gate="In Zernio as a draft, not yet live. Publishing is a human act performed there, not here — this app can only notice that it happened."
              queue={inbox.data.pushed_awaiting_monte}
              /* Still the one gate with no in-app destination — the clearing act happens in
                 Zernio's own dashboard, the only Zernio URL in this repo is the API root
                 (`config.py:71`), and a synthesized dashboard URL would be a guess that looks
                 like a fact. What US-012 could fix is the near end: the item id is this draft's
                 id, so the trail now resumes at the exact draft rather than at an empty Studio,
                 and that page states the `zernio_post_id` it is waiting on. */
              href={(item) => `/studio?draft=${item.id}`}
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
        </>
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
            {/* Not `health.ok` — inside this branch that is `true` by construction, so the row
                could only ever say "ok". The backend's own verdict on itself is what the row is
                for: it answered, and it may have answered that it is unhealthy.
                ponytail: reused as the existing ok/down badge rather than given a vocabulary of
                its own. "down" for a backend that plainly answered is blunt, but every other row
                in this strip is a boolean and one word is what a footer has room for. Ceiling: a
                real status vocabulary here once /health emits more than the literal "ok" it
                hardcodes today (backend/app/main.py:82). */}
            <HealthRow label="API" ok={health.data.status === "ok"} />
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
