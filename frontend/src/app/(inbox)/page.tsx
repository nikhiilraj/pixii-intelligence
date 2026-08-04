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

const STALLED_DAYS = 7;
const WORKLIST_CAP = 8;

type GateKey =
  | "proposals_awaiting_review"
  | "built_awaiting_push"
  | "pushed_awaiting_monte"
  | "published_awaiting_verdict";

type Gate = {
  key: GateKey;
  short: string;
  label: string;
  clearsAt: string;
  href: (item: InboxItem) => string | null;
};

const GATES: Gate[] = [
  {
    key: "proposals_awaiting_review",
    short: "review",
    label: "awaiting review",
    clearsAt: "Templates",
    href: () => "/templates",
  },
  {
    key: "built_awaiting_push",
    short: "push",
    label: "awaiting push",
    clearsAt: "Studio",
    href: (item) => `/studio?draft=${item.id}`,
  },
  {
    key: "pushed_awaiting_monte",
    short: "Monte",
    label: "awaiting Monte",
    clearsAt: "Zernio",
    href: (item) => `/studio?draft=${item.id}`,
  },
  {
    key: "published_awaiting_verdict",
    short: "verdict",
    label: "awaiting verdict",
    clearsAt: "Post",
    href: (item) => `/posts/${item.id}`,
  },
];

function waited(days: number): string {
  if (days <= 0) return "arrived today";
  if (days === 1) return "waiting 1 day";
  return `waiting ${days} days`;
}

function compactAge(days: number): string {
  if (days <= 0) return "today";
  if (days === 1) return "1 day";
  return `${days} days`;
}

function oldest(queue: InboxQueue): number {
  return queue.items.reduce((age, item) => Math.max(age, item.age_days), 0);
}

function Circuit({ inbox }: { inbox: Inbox }) {
  const queues = GATES.map((gate) => inbox[gate.key]);
  const furthest = queues.reduce((last, queue, index) => (queue.count > 0 ? index : last), 0);
  const nodes = [96, 405, 714, 1023];
  const progressX = nodes[furthest];

  return (
    <section aria-labelledby="circuit-title" className="border-y border-border bg-surface">
      <h2 id="circuit-title" className="sr-only">
        Lineage circuit
      </h2>
      <div className="mx-auto max-w-6xl px-6">
        <div className="grid grid-cols-2 gap-px bg-border md:hidden">
          {GATES.map((gate, index) => {
            const queue = inbox[gate.key];
            return (
              <div key={gate.key} className="bg-surface px-4 py-4">
                <div className="flex items-center gap-2">
                  <span
                    aria-hidden="true"
                    className={`size-1.5 ${queue.count > 0 ? "bg-text" : "border border-absent"}`}
                  />
                  <span className="font-mono text-head tabular-nums">{queue.count}</span>
                </div>
                <p className="mt-1 font-mono text-caption text-muted">
                  0{index + 1} · {gate.short}
                </p>
              </div>
            );
          })}
        </div>

        <div className="relative hidden h-[170px] md:block">
          <svg
            aria-hidden="true"
            viewBox="0 0 1104 170"
            className="absolute inset-0 size-full"
          >
            <path d="M96 48 H1023" fill="none" stroke="var(--border)" strokeWidth="1.5" />
            <path
              d={`M96 48 H${progressX}`}
              fill="none"
              stroke="var(--text)"
              strokeWidth="1.5"
            />
            <path
              d="M1023 48 C1085 48 1085 140 1023 140 L96 140 C34 140 34 48 96 48"
              fill="none"
              stroke={inbox.closed_circuits > 0 ? "var(--text)" : "var(--border)"}
              strokeWidth="1.5"
              strokeDasharray={inbox.closed_circuits > 0 ? undefined : "5 5"}
            />
            {nodes.map((x, index) => {
              const queue = queues[index];
              const stalled = oldest(queue) >= STALLED_DAYS;
              return (
                <g key={x}>
                  {stalled && (
                    <circle cx={x} cy="48" r="16" fill="none" stroke="var(--accent)" strokeWidth="2" />
                  )}
                  <circle
                    cx={x}
                    cy="48"
                    r="6"
                    fill={queue.count > 0 ? "var(--text)" : "var(--bg)"}
                    stroke={queue.count > 0 ? "var(--text)" : "var(--absent)"}
                    strokeWidth="1.5"
                  />
                </g>
              );
            })}
          </svg>

          {GATES.map((gate, index) => {
            const queue = inbox[gate.key];
            const age = oldest(queue);
            return (
              <div
                key={gate.key}
                className="absolute top-3 -translate-x-1/2 text-center"
                style={{ left: `${(nodes[index] / 1104) * 100}%` }}
              >
                <p className="font-mono text-[28px] leading-8 tabular-nums">{queue.count}</p>
                <p className="mt-9 font-mono text-caption text-muted">
                  0{index + 1} · {gate.label}
                </p>
                <p className="font-mono text-caption text-muted">
                  {queue.count > 0 ? `oldest ${waited(age)}` : "gate clear"}
                </p>
              </div>
            );
          })}

          <p className="absolute inset-x-0 bottom-4 text-center font-mono text-caption text-muted">
            {inbox.closed_circuits === 0
              ? "the return path · 0 laps · nothing has ever travelled it"
              : `${inbox.closed_circuits} laps · the circuit has run end to end`}
          </p>
        </div>
      </div>
    </section>
  );
}

type WorkItem = { item: InboxItem; gate: Gate; gateIndex: number };

function Worklist({ inbox }: { inbox: Inbox }) {
  const items: WorkItem[] = GATES.flatMap((gate, gateIndex) =>
    inbox[gate.key].items.map((item) => ({ item, gate, gateIndex })),
  ).sort((a, b) => b.item.age_days - a.item.age_days || a.gateIndex - b.gateIndex);
  const total = GATES.reduce((sum, gate) => sum + inbox[gate.key].count, 0);
  const shown = items.slice(0, WORKLIST_CAP);

  return (
    <section aria-labelledby="worklist-title" className="mt-12">
      <div className="flex items-end justify-between gap-4 border-b border-text pb-3">
        <div>
          <p className="font-mono text-caption uppercase tracking-[0.12em] text-muted">Oldest first</p>
          <h2 id="worklist-title" className="mt-1 text-head font-medium">
            Work waiting on a person
          </h2>
        </div>
        <span className="font-mono text-meta tabular-nums text-muted">
          showing {Math.min(total, WORKLIST_CAP)} of {total}
        </span>
      </div>

      {shown.length === 0 ? (
        <Card className="mt-4 border-dashed bg-surface-2 text-body text-muted">
          Nothing is waiting. The circuit is clear; the next item will appear here when it reaches a human gate.
        </Card>
      ) : (
        <div className="overflow-hidden rounded-card border border-border bg-surface">
          <div className="hidden grid-cols-[104px_minmax(0,1fr)_210px_100px] bg-surface-2 px-5 py-3 font-mono text-[10.5px] uppercase tracking-[0.12em] text-muted md:grid">
            <span>Waited</span>
            <span>Item</span>
            <span>Gate</span>
            <span>Clears at</span>
          </div>
          <ul>
            {shown.map(({ item, gate, gateIndex }) => {
              const href = gate.href(item);
              return (
                <li
                  key={`${gate.key}-${item.id}`}
                  className="border-t border-border-subtle first:border-t-0 md:first:border-t"
                >
                  <div className="grid gap-2 px-5 py-4 hover:bg-surface-2 md:grid-cols-[104px_minmax(0,1fr)_210px_100px] md:items-center md:gap-0">
                    <span className="flex items-center gap-2 font-mono text-meta tabular-nums">
                      <span
                        aria-hidden="true"
                        className={`size-1.5 shrink-0 ${
                          item.age_days >= STALLED_DAYS ? "bg-accent" : "bg-transparent"
                        }`}
                      />
                      {compactAge(item.age_days)}
                    </span>
                    {href ? (
                      <Link href={href} className="min-w-0 truncate text-body hover:underline">
                        {item.label}
                      </Link>
                    ) : (
                      <span className="min-w-0 truncate text-body">{item.label}</span>
                    )}
                    <span className="font-mono text-caption text-muted">
                      0{gateIndex + 1} · {gate.label}
                    </span>
                    {href && gateIndex !== 2 ? (
                      <Link href={href} className="text-meta font-medium text-accent-text hover:underline">
                        {gate.clearsAt} →
                      </Link>
                    ) : (
                      <span className="text-meta text-muted">{gate.clearsAt}</span>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
          {total > shown.length && (
            <p className="border-t border-border-subtle px-5 py-3 text-meta text-muted">
              {total - shown.length} more waiting. Open the gate destination to work through the rest.
            </p>
          )}
        </div>
      )}
    </section>
  );
}

function Circuits({ closed }: { closed: number }) {
  return (
    <section className="mt-14 grid gap-6 border-t border-text pt-8 md:grid-cols-[240px_1fr] md:items-center">
      <div className="flex items-end gap-4">
        <span className="font-mono text-[56px] leading-[0.85] tabular-nums md:text-[84px]">{closed}</span>
        <div>
          <p className="font-mono text-caption uppercase tracking-[0.12em] text-muted">closed circuits</p>
          <p className="mt-1 text-meta text-muted">full laps through the system</p>
        </div>
      </div>
      <p className="max-w-2xl text-body text-muted">
        {closed === 0
          ? "The circuit has never been round: no draft this app generated has been pushed, published and ruled on, not once. A pushed draft that Monte publishes and someone then rules on makes this 1."
          : "Drafts written here that were pushed to Zernio, published by a human, and then ruled on — one full lap each."}{" "}
        A count of laps, not a score: it says the circuit ran, never that a post did well.
      </p>
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
  const [inbox, health] = await Promise.all([
    getJson<Inbox>("/inbox"),
    getJson<Health>("/health"),
  ]);

  const total = inbox.ok
    ? GATES.reduce((sum, gate) => sum + inbox.data[gate.key].count, 0)
    : 0;
  const bottleneck = inbox.ok
    ? GATES.reduce((current, gate) =>
        inbox.data[gate.key].count > inbox.data[current.key].count ? gate : current,
      )
    : GATES[0];
  const bottleneckCount = inbox.ok ? inbox.data[bottleneck.key].count : 0;
  const bottleneckHref = inbox.ok
    ? bottleneck.href(inbox.data[bottleneck.key].items[0] ?? { id: 0, label: "", waiting_since: "", age_days: 0 })
    : null;

  return (
    <>
      {inbox.ok && <Circuit inbox={inbox.data} />}

      <main className="mx-auto max-w-6xl px-6 pb-16 pt-12">
        <header className="grid gap-8 lg:grid-cols-[1fr_auto] lg:items-end">
          <div>
            <p className="font-mono text-caption uppercase tracking-[0.12em] text-muted">Today’s work</p>
            <h1 className="mt-2 max-w-3xl text-display font-semibold tracking-[-0.02em]">
              {inbox.ok ? `${total} ${total === 1 ? "thing is" : "things are"} waiting on you` : "Inbox"}
            </h1>
            <p className="mt-3 max-w-2xl text-body text-muted">
              The four human gates of the lineage circuit, ordered by wait time. These are queues, not scores: nothing here claims a template, draft or post performed well or badly.
            </p>
          </div>

          {inbox.ok && bottleneckCount > 0 && bottleneckHref && (
            <div className="lg:text-right">
              <Link
                href={bottleneckHref}
                className="inline-flex min-h-10 items-center justify-center rounded-input bg-text px-4 py-2 text-meta font-medium text-bg transition-colors hover:bg-text/90"
              >
                {bottleneck.key === "proposals_awaiting_review"
                  ? `Review ${bottleneckCount} proposals →`
                  : `Open ${bottleneckCount} at ${bottleneck.short} →`}
              </Link>
              <p className="mt-2 font-mono text-caption text-muted">The largest queue sets the next action.</p>
            </div>
          )}
        </header>

        {inbox.ok ? (
          <>
            <Worklist inbox={inbox.data} />
            {inbox.data.published_awaiting_verdict.count === 0 && (
              <div className="mt-6 flex gap-3 border border-dashed border-border px-4 py-3 text-meta text-muted">
                <span aria-hidden="true" className="h-5 w-8 shrink-0 pixii-hatch" />
                <p>
                  Nothing has ever arrived at gate 04. A generated post appears here only after a human publishes it from Zernio.
                </p>
              </div>
            )}
            <Circuits closed={inbox.data.closed_circuits} />
          </>
        ) : (
          <ApiFailureNotice failure={inbox} className="mt-10" />
        )}

        <footer className="mt-16 border-t border-border pt-6">
          <h2 className="font-mono text-caption uppercase tracking-[0.12em] text-muted">System status</h2>
          {health.ok ? (
            <div className="mt-2 flex flex-wrap items-center gap-x-5 gap-y-2">
              <HealthRow label="API" ok={health.data.status === "ok"} />
              <HealthRow label="Database" ok={health.data.database} />
              {Object.entries(health.data.credentials).map(([name, present]) => (
                <HealthRow key={name} label={name} ok={present} />
              ))}
            </div>
          ) : (
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
    </>
  );
}
