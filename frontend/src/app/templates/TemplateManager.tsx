"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import { Badge, type BadgeProps } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { postBlob, postJson, type Cohort, type Template, type TemplateKind } from "@/lib/api";

import TemplatePreview from "./TemplatePreview";

const KINDS: TemplateKind[] = ["hook", "structure", "visual"];

// Which body of work extraction reads. A hook or a structure is a borrowable shape, so
// either cohort may teach one; a voice is not borrowable, and the backend enforces that
// independently of what is chosen here.
const COHORTS: { value: Cohort; label: string }[] = [
  { value: "voice", label: "from our own posts" },
  { value: "inspiration", label: "from creator posts" },
];

const COHORT_LABEL: Record<string, string> = {
  voice: "proven in our own posts",
  inspiration: "borrowed from a creator",
};

const COHORT_STYLE: Record<string, string> = {
  voice: "bg-black/8 dark:bg-white/10",
  inspiration: "bg-violet-500/15 text-violet-700 dark:text-violet-300",
};

/** The cohort extraction recorded, or null for a hand-authored template that has none.
 *
 * It lives in `body`, not `provenance` — that list is typed `list[str]` and read
 * positionally by `generation._exemplars`. A template written through the form below never
 * has one, and naming a cohort it was not read from would be worse than naming none.
 */
function cohortOf(template: Template): string | null {
  const cohort = template.body.cohort;
  return typeof cohort === "string" ? cohort : null;
}

/** The path an extract button posts to, cohort and all.
 *
 *  A function rather than three inline template strings because `cohort` is the one value on
 *  this page that reaches a query string, and the Radix trigger that sets it cannot be driven
 *  in jsdom — so this is the only place the mapping from a chosen cohort to the sent query can
 *  be asserted for both cohorts. All three call sites go through it; leaving one inline would
 *  put a second copy where a mutation could hide.
 *
 *  The structures path used to carry `sample_size=27` as well. That parameter is gone from the
 *  route: extraction reads the whole corpus, so there is no smaller sample to ask for. Left in
 *  place it would still have been *sent* — FastAPI ignores an unknown query parameter rather
 *  than refusing it — and a number this page appeared to choose would have decided nothing. */
export function extractPath(what: "hooks" | "structures" | "visuals", cohort: Cohort): string {
  return `/templates/extract/${what}?${new URLSearchParams({ cohort })}`;
}

const BLANK_BODY: Record<TemplateKind, string> = {
  hook: JSON.stringify({ pattern: "{value} turned into {outcome}", tone: "direct" }, null, 2),
  structure: JSON.stringify(
    { sections: [{ name: "open", guidance: "" }], compatible_hooks: [] },
    null,
    2,
  ),
  visual: JSON.stringify({ renderer: "html", component: "stat_hero" }, null, 2),
};

/** A template's status, carried by the shared Badge — the fill says the status, the word stays
 *  on --text.
 *
 *  This file used to declare its own `Badge` over a local map of raw palette colours
 *  (`text-emerald-700`, `text-amber-700`, plus `opacity-60` on retired), which is precisely the
 *  shape `components/ui/badge.tsx` exists to prevent and its header comment measures. Measured
 *  on the rendered page, all three light-mode states were under the 4.5 floor as text —
 *  proposed 4.31:1, retired 4.34:1, approved 4.47:1 — while the shared component's labels sit at
 *  13:1 or better in both themes because it tints the status behind a `--text` word instead of
 *  colouring the word. Three near-misses, on the page where 50 proposals are reviewed.
 *
 *  An unknown status falls through to the Badge's own `neutral` default rather than to an
 *  unstyled span; the old map's `?? ""` rendered a bare word with no affordance at all. */
const STATUS_VARIANT: Record<string, BadgeProps["variant"]> = {
  approved: "success",
  proposed: "warning",
  retired: "neutral",
};

/** Whose posts a template was read from.
 *
 * While reviewing a proposal this is the difference between a shape proven in our own
 * posts and one borrowed from a creator.
 *
 * Three states, three renderings. Most of the queue predates the field, and rendering
 * nothing for those would put a blank beside rows explicitly marked as borrowed — which
 * reads as "not borrowed", a claim the row does not carry. Unrecorded is the true one.
 *
 * "Unrecorded" is muted with --text-muted, not with `opacity-40`. Opacity was the worst
 * contrast in the app — 2.51:1 light, 3.40:1 dark, the only element that failed AA in both
 * themes — and it failed on the one label that tells a reviewer whether a proposal came from
 * our own posts or was borrowed. A third state that says "we do not know" still has to be
 * readable to say it; the token mutes to a measured 5.2:1 instead of to an arbitrary alpha.
 */
function CohortTag({ template }: { template: Template }) {
  const cohort = cohortOf(template);
  if (!cohort) {
    return <span className="rounded px-1.5 py-0.5 text-xs text-muted">cohort unrecorded</span>;
  }
  return (
    <span className={`rounded px-1.5 py-0.5 text-xs ${COHORT_STYLE[cohort] ?? ""}`}>
      {COHORT_LABEL[cohort] ?? cohort}
    </span>
  );
}

function Key({ children }: { children: React.ReactNode }) {
  return (
    <kbd className="rounded border border-current/25 px-1.5 py-0.5 font-mono text-[10px] font-normal leading-none">
      {children}
    </kbd>
  );
}

function producedBy(template: Template): { raw: string; example: string } {
  if (typeof template.body.pattern === "string") {
    const raw = template.body.pattern;
    const examples = Object.fromEntries(
      template.slots.map((slot) => [String(slot.name), String(slot.example ?? slot.name ?? "")]),
    );
    const example = raw.replace(/\{([^}]+)\}/g, (_match, name: string) => {
      const supplied = examples[name];
      return supplied || name.replaceAll("_", " ");
    });
    return { raw, example };
  }

  if (Array.isArray(template.body.sections)) {
    const sections = template.body.sections
      .filter((section): section is Record<string, unknown> => typeof section === "object" && section !== null)
      .slice(0, 4);
    return {
      raw: sections.map((section) => String(section.name ?? "section")).join(" → "),
      example: sections
        .map((section) => String(section.guidance ?? section.name ?? ""))
        .filter(Boolean)
        .join("\n"),
    };
  }

  const renderer = String(template.body.renderer ?? template.body.component ?? "visual");
  return {
    raw: `${renderer} · ${template.slots.length} declared slot${template.slots.length === 1 ? "" : "s"}`,
    example:
      template.slots.length > 0
        ? template.slots.map((slot) => String(slot.example ?? slot.name ?? "slot")).join(" · ")
        : "A rendered visual. Preview it before approving the shape.",
  };
}

export default function TemplateManager({
  initial,
  defaultMode = "list",
}: {
  initial: Template[];
  defaultMode?: "review" | "list";
}) {
  const router = useRouter();
  const [kind, setKind] = useState<TemplateKind>("hook");
  const [cohort, setCohort] = useState<Cohort>("voice");
  const [name, setName] = useState("");
  const [body, setBody] = useState(BLANK_BODY.hook);
  const [editing, setEditing] = useState<Template | null>(null);
  const [busy, setBusy] = useState(false);
  const [preview, setPreview] = useState<{ id: number; url: string } | null>(null);
  const [mode, setMode] = useState<"review" | "list">(defaultMode);
  const [cursor, setCursor] = useState(0);
  const [deferred, setDeferred] = useState<number[]>([]);
  const [session, setSession] = useState({ approved: 0, retired: 0 });

  const proposals = initial.filter((template) => template.status === "proposed");
  const queue = [
    ...proposals.filter((template) => !deferred.includes(template.id)),
    ...proposals.filter((template) => deferred.includes(template.id)),
  ];
  const current = queue[Math.min(cursor, Math.max(queue.length - 1, 0))];
  const produced = current ? producedBy(current) : null;
  const cleared = session.approved + session.retired;

  async function renderPreview(template: Template) {
    setBusy(true);

    // Slot examples are what the template author wrote down as representative, so
    // they are the honest default for a preview.
    const values = Object.fromEntries(
      template.slots.map((slot) => [String(slot.name), String(slot.example ?? slot.name ?? "")]),
    );
    // The only endpoint that answers with bytes — hence postBlob. Its 502-on-Cloudflare-429
    // detail (see US-007) is worth reading verbatim, since "rate limited, retry" and "broken"
    // are the same red line otherwise.
    const result = await postBlob(`/templates/${template.id}/preview`, values);
    setBusy(false);

    if (result.ok) setPreview({ id: template.id, url: URL.createObjectURL(result.data) });
    else toast.error("Preview failed", { description: result.message });
  }

  async function send(path: string, body?: unknown, method: "POST" | "PUT" = "POST") {
    setBusy(true);

    const result = await postJson(path, body, method);
    setBusy(false);

    if (!result.ok) {
      // Message only, no title — `send` covers seven mutations and a URL path is not a title
      // to show a reader. Same reasoning as Studio's `call`.
      toast.error(result.message);
      return false;
    }
    router.refresh();
    return true;
  }

  function parseBody(): unknown | null {
    try {
      return JSON.parse(body);
    } catch {
      // The only client-side failure here, and it reported into the same shared paragraph as
      // the API failures rather than beside the textarea — so a toast loses no locality it
      // had. It is not an API detail and is deliberately outside the toast test.
      toast.error("Body is not valid JSON.");
      return null;
    }
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    const parsed = parseBody();
    if (parsed === null) return;

    const ok = editing
      ? await send(`/templates/${editing.id}`, { name, body: parsed }, "PUT")
      : await send("/templates", { kind, name, body: parsed });

    if (ok) reset();
  }

  function reset() {
    setEditing(null);
    setName("");
    setBody(BLANK_BODY[kind]);
  }

  function startEdit(template: Template) {
    setEditing(template);
    setKind(template.kind);
    setName(template.name);
    setBody(JSON.stringify(template.body, null, 2));
  }

  function move(amount: number) {
    if (queue.length === 0) return;
    setCursor((at) => Math.min(Math.max(at + amount, 0), queue.length - 1));
  }

  function decideLater() {
    if (!current) return;
    setDeferred((ids) => (ids.includes(current.id) ? ids : [...ids, current.id]));
    setCursor(0);
  }

  async function decide(action: "approve" | "retire") {
    if (!current) return;
    const ok = await send(`/templates/${current.id}/${action}`);
    if (!ok) return;
    setSession((value) => ({ ...value, [action === "approve" ? "approved" : "retired"]: value[action === "approve" ? "approved" : "retired"] + 1 }));
    setCursor((at) => Math.min(at, Math.max(queue.length - 2, 0)));
  }

  useEffect(() => {
    if (mode !== "review" || !current || busy) return;

    function onKeyDown(event: KeyboardEvent) {
      const target = event.target as HTMLElement | null;
      if (target?.matches("input, textarea, select, [contenteditable='true']")) return;
      const key = event.key.toLowerCase();
      if (!["a", "r", "e", "s", "j", "k"].includes(key)) return;
      event.preventDefault();
      if (key === "a") void decide("approve");
      if (key === "r") void decide("retire");
      if (key === "e") {
        startEdit(current);
        setMode("list");
      }
      if (key === "s") decideLater();
      if (key === "j") move(1);
      if (key === "k") move(-1);
    }

    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  });

  // Dresses the name input and the body textarea, now that the kind select draws its own border
  // from the same tokens. Retokenised with the select that left it: leaving `border-black/15`
  // here would put a differently-weighted border on the two controls directly under a
  // `border-border` trigger, which is the mismatch this slice exists to remove.
  const field = "w-full rounded-input border border-border bg-transparent px-3 py-2 text-meta";

  return (
    <div className="mt-8">
      <div className="-mx-6 flex flex-wrap items-center gap-2 border-y border-border bg-surface px-6 py-3 text-meta">
        {(["proposed", "approved", "retired"] as const).map((status) => (
          <span key={status} className={status === "proposed" ? "font-medium text-text" : "text-muted"}>
            {status[0].toUpperCase() + status.slice(1)}{" "}
            <span className="font-mono tabular-nums">
              {initial.filter((template) => template.status === status).length}
            </span>
          </span>
        ))}
        <span className="ml-auto font-mono text-caption text-muted">
          {cleared} cleared this session
        </span>
        <div className="flex rounded-input border border-border p-0.5" aria-label="Template view">
          {(["review", "list"] as const).map((view) => (
            <button
              key={view}
              type="button"
              aria-pressed={mode === view}
              onClick={() => setMode(view)}
              className="min-h-8 rounded-[4px] px-3 text-caption font-medium capitalize text-muted aria-pressed:bg-text aria-pressed:text-bg"
            >
              {view}
            </button>
          ))}
        </div>
      </div>

      {mode === "review" ? (
        <div className="mt-10 grid gap-9 lg:grid-cols-[minmax(0,1fr)_20.5rem]">
          <section className="min-w-0">
            {!current ? (
              <Card className="border-dashed bg-surface-2 p-6">
                <p className="text-head font-medium">The review queue is clear.</p>
                <p className="mt-2 max-w-2xl text-body text-muted">
                  Every proposal has been approved, retired or deferred. The library can now do the work it was built for.
                </p>
                <Link href="/studio" className="mt-5 inline-block text-meta font-medium text-accent-text hover:underline">
                  Write something in Studio →
                </Link>
              </Card>
            ) : (
              <>
                <div className="flex flex-wrap items-center justify-between gap-3 font-mono text-caption uppercase tracking-[0.12em] text-muted">
                  <span>
                    Proposal {Math.min(cursor + 1, queue.length)} of {queue.length} · {current.kind} · v{current.version}
                  </span>
                  <span className="normal-case tracking-normal">Use J / K to move</span>
                </div>
                <h2 className="mt-4 text-display font-semibold tracking-[-0.02em]">{current.name}</h2>
                <div className="mt-3 flex flex-wrap items-center gap-2">
                  <Badge variant={STATUS_VARIANT[current.status]}>{current.status}</Badge>
                  <CohortTag template={current} />
                  {/* Same rule as the list row's coverage line, and it was unguarded here: this
                      printed "read from 0 posts" for every hand-authored template and all 15
                      structures now in the library, none of which had a source recorded rather
                      than measured as none. The prose section below already said the true thing,
                      which is probably why this survived — but a reader decides on the header. */}
                  <span className="text-caption text-muted">
                    read from{" "}
                    {current.provenance.length > 0
                      ? `${current.provenance.length} post${current.provenance.length === 1 ? "" : "s"}`
                      : "—"}
                  </span>
                </div>

                <section className="mt-10 border-t border-text pt-5">
                  <p className="font-mono text-caption uppercase tracking-[0.12em] text-muted">What it produces</p>
                  <p className="mt-4 overflow-x-auto rounded-input bg-surface-2 px-4 py-3 font-mono text-caption text-muted">
                    {produced?.raw}
                  </p>
                  <p className="mt-5 whitespace-pre-wrap text-[20px] leading-[30px]">{produced?.example}</p>
                  <p className="mt-3 text-meta text-muted">This is the shape being approved, not a prediction about how it will do.</p>
                </section>

                <section className="mt-9">
                  <p className="font-mono text-caption uppercase tracking-[0.12em] text-muted">Read from</p>
                  <p className="mt-3 text-body">
                    {current.provenance.length > 0
                      ? `${current.provenance.length} source post${current.provenance.length === 1 ? "" : "s"} supplied this pattern.`
                      : "No source post was recorded for this proposal."}
                  </p>
                  <p className="mt-1 text-meta text-muted">Provenance says where the pattern came from. It is not evidence that the pattern will travel.</p>
                </section>

                <details className="mt-8 border-y border-dashed border-border py-3">
                  <summary className="cursor-pointer font-mono text-caption text-muted">Show body JSON</summary>
                  <pre className="mt-3 max-h-80 overflow-auto rounded-input bg-surface-2 p-4 font-mono text-caption">
                    {JSON.stringify(current.body, null, 2)}
                  </pre>
                </details>

                {preview?.id === current.id && (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img src={preview.url} alt={`Preview of ${current.name}`} className="mt-6 w-full max-w-sm rounded-card border border-border" />
                )}

                <div className="mt-8 flex flex-wrap gap-2 border-t border-border pt-5">
                  <Button onClick={() => void decide("approve")} disabled={busy}>Approve <Key>A</Key></Button>
                  <Button variant="outline" onClick={() => void decide("retire")} disabled={busy}>Retire <Key>R</Key></Button>
                  <Button variant="outline" onClick={() => { startEdit(current); setMode("list"); }} disabled={busy}>Edit body <Key>E</Key></Button>
                  {current.kind === "visual" && <Button variant="outline" onClick={() => void renderPreview(current)} disabled={busy}>Preview</Button>}
                  <Button variant="outline" onClick={decideLater} disabled={busy}>Decide later <Key>S</Key></Button>
                </div>
                <p className="mt-4 max-w-2xl text-meta text-muted">
                  Approving makes the shape usable; it generates nothing. Retiring keeps the row and its history. Deciding later moves it to the back without resetting its age.
                </p>
              </>
            )}
          </section>

          <aside className="space-y-5">
            <Card className="p-5">
              <div className="flex items-end justify-between gap-3">
                <p className="font-mono text-display tabular-nums">{cleared}</p>
                <p className="pb-1 font-mono text-caption text-muted">of {proposals.length} this session</p>
              </div>
              <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-surface-2">
                <div className="h-full bg-accent" style={{ width: `${proposals.length ? (cleared / proposals.length) * 100 : 0}%` }} />
              </div>
              <p className="mt-3 text-meta text-muted">{session.approved} approved, {session.retired} retired. Work done, not a score.</p>
            </Card>
            <div>
              <p className="font-mono text-caption uppercase tracking-[0.12em] text-muted">Next in the queue</p>
              <ol className="mt-2 border-y border-border">
                {queue.slice(cursor + 1, cursor + 4).map((template) => (
                  <li key={template.id} className="border-b border-border-subtle py-3 last:border-0">
                    <p className="truncate text-meta font-medium">{template.name}</p>
                    <p className="font-mono text-caption text-muted">{template.kind} · v{template.version}</p>
                  </li>
                ))}
              </ol>
            </div>
            <Card className="bg-surface-2 p-5">
              <p className="font-medium">When this queue is empty</p>
              <p className="mt-2 text-meta text-muted">The approved library is ready to turn an idea into a traceable draft.</p>
              <Link href="/studio" className="mt-4 inline-block text-meta font-medium text-accent-text hover:underline">Write something in Studio →</Link>
            </Card>
            <Link href="/scoreboard" className="block text-meta text-muted hover:text-text">Open the Scoreboard →</Link>
          </aside>
        </div>
      ) : (
      <div className="mt-8 grid gap-10 lg:grid-cols-[1fr_20rem]">
      {/* `min-w-0` is the whole reason this page is not 10384px wide at a 1440px viewport. A grid
          track carries an implicit `min-width: auto` that resolves to its item's min-content, and
          this section's min-content is the longest unwrapped line of the `JSON.stringify` dump in
          the `<pre>` below — so the track grew to fit it and the `overflow-x-auto` already on that
          `<pre>` was never asked to scroll. The author column then stretched to match. Do not
          delete this class because it looks inert; the overflow it prevents is data-dependent and
          only shows up once a template body contains a long line. */}
      <section className="min-w-0">
        <div className="mb-4 flex flex-wrap items-center gap-3">
          {/* No sentinel on either Select in this file: both are required choices with a real
              default ("voice", "hook"), so neither has an unset state and Radix's ban on an
              empty item value never bites. Worth naming rather than leaving as an absence —
              `cohort` *does* reach a query string (`/templates/extract/hooks?cohort=`), so the
              day this grows an "any cohort" option it needs the sentinel and the query test
              that /posts and /assets carry. */}
          <Select value={cohort} onValueChange={(v) => setCohort(v as Cohort)} disabled={busy}>
            <SelectTrigger aria-label="extraction cohort">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {COHORTS.map((c) => (
                <SelectItem key={c.value} value={c.value}>
                  {c.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Button
            variant="outline"
            onClick={() => send(extractPath("hooks", cohort))}
            disabled={busy}
          >
            {busy ? "Working…" : "Extract hooks from top posts"}
          </Button>
          <Button
            variant="outline"
            onClick={() => send(extractPath("structures", cohort))}
            disabled={busy}
          >
            Extract structures
          </Button>
          <Button
            variant="outline"
            onClick={() => send(extractPath("visuals", cohort))}
            disabled={busy}
          >
            Extract visual layouts
          </Button>
          <span className="text-xs text-muted">
            Proposes patterns from the strongest posts in the chosen cohort. Visual extraction
            takes longer because every proposal is test-rendered. Nothing becomes usable until
            you approve it.
          </span>
        </div>

        {initial.length === 0 ? (
          /* Designed rather than apologetic: it states what this list holds and the two ways
             something gets into it. This is a genuinely empty library and never a failed read —
             `page.tsx` renders ApiFailureNotice in place of this whole component when the read
             fails, so nothing can reach here as `[]` because of an error. */
          <Card className="bg-surface-2 text-body">
            <p className="font-medium">No template exists yet.</p>
            <p className="mt-1 text-muted">
              Extraction reads the corpus and proposes hooks, structures and visuals — the three
              buttons above — and nothing it proposes becomes usable until you approve it. A
              template can also be written by hand in the form beside this list.
            </p>
          </Card>
        ) : (
          <ul className="space-y-2">
            {initial.map((t) => (
              <li
                key={t.id}
                className="rounded-lg border border-black/10 p-3 dark:border-white/15"
              >
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-medium">{t.name}</span>
                  <span className="text-xs text-muted">
                    {t.kind} · v{t.version}
                  </span>
                  <Badge variant={STATUS_VARIANT[t.status]}>{t.status}</Badge>
                  <CohortTag template={t} />
                  <span className="ml-auto flex gap-3 text-xs">
                    {t.status !== "retired" && (
                      <button
                        onClick={() => startEdit(t)}
                        disabled={busy}
                        className="underline opacity-70 hover:opacity-100"
                      >
                        edit
                      </button>
                    )}
                    {t.status === "proposed" && (
                      <button
                        onClick={() => send(`/templates/${t.id}/approve`)}
                        disabled={busy}
                        className="underline opacity-70 hover:opacity-100"
                      >
                        approve
                      </button>
                    )}
                    {t.kind === "visual" && (
                      <button
                        onClick={() => renderPreview(t)}
                        disabled={busy}
                        className="underline opacity-70 hover:opacity-100"
                      >
                        preview
                      </button>
                    )}
                    {t.status !== "retired" && (
                      <button
                        onClick={() => send(`/templates/${t.id}/retire`)}
                        disabled={busy}
                        className="underline opacity-70 hover:opacity-100"
                      >
                        retire
                      </button>
                    )}
                  </span>
                </div>
                {/* How many corpus posts this template covers. It is on every row, including
                    the empty one — the old `provenance.length > 0 &&` guard rendered nothing
                    there, so a row that covers nothing and a row whose coverage was never
                    recorded were the same blank, and reading either meant writing SQL.

                    Empty provenance prints `—`, never `0`. Every hand-authored template has
                    one, as do the 15 structures in the library today: no extraction ever
                    recorded a source for them, which is not the same claim as "extraction
                    looked and found no post". The units go with the number and not with the
                    dash, because "— posts" would be that same claim in another shape.

                    A count, and deliberately nothing more: no sort control, no ordering by it,
                    no "top". ~3 samples across a 12.7× spread does not support a ranking. */}
                <p className="mt-1 text-xs text-muted">
                  coverage{" "}
                  <span className="font-mono tabular-nums">
                    {t.provenance.length > 0
                      ? `${t.provenance.length} post${t.provenance.length === 1 ? "" : "s"}`
                      : "—"}
                  </span>
                </p>
                {preview?.id === t.id && (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img
                    src={preview.url}
                    alt={`Preview of ${t.name}`}
                    className="mt-2 w-full max-w-xs rounded border border-black/10 dark:border-white/15"
                  />
                )}
                <pre className="mt-2 overflow-x-auto rounded bg-black/5 p-2 text-xs dark:bg-white/10">
                  {JSON.stringify(t.body, null, 2)}
                </pre>
              </li>
            ))}
          </ul>
        )}
      </section>

      <form onSubmit={submit} className="space-y-3">
        <h2 className="text-xs font-medium uppercase tracking-widest text-muted">
          {editing ? `Revise "${editing.name}" → v${editing.version + 1}` : "Author a template"}
        </h2>

        {!editing && (
          <Select
            value={kind}
            onValueChange={(value) => {
              const next = value as TemplateKind;
              setKind(next);
              setBody(BLANK_BODY[next]);
            }}
          >
            <SelectTrigger aria-label="template kind" className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {KINDS.map((k) => (
                <SelectItem key={k} value={k}>
                  {k}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}

        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="name"
          required
          className={field}
        />

        <textarea
          value={body}
          onChange={(e) => setBody(e.target.value)}
          rows={12}
          spellCheck={false}
          className={`${field} font-mono text-xs`}
        />

        {/* Only a visual has a renderer for `POST /templates/preview` to run — a hook or a
            structure has none, and the endpoint's 501 on anything but `html` exists precisely
            because it cannot check `kind` the way the saved route does (see `preview_unsaved`
            in api_templates.py). Mounting this for every kind would just move that refusal from
            "never offered" to "offered and then refused". */}
        {kind === "visual" && <TemplatePreview body={body} slots={editing?.slots ?? []} />}

        <div className="flex gap-3">
          <Button type="submit" disabled={busy}>
            {editing ? "Save as new version" : "Create"}
          </Button>
          {editing && (
            <button type="button" onClick={reset} className="text-sm underline opacity-70">
              cancel
            </button>
          )}
        </div>
      </form>
      </div>
      )}
    </div>
  );
}
