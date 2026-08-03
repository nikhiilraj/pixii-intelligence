"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
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
 *  A function rather than two inline template strings because `cohort` is the one value on this
 *  page that reaches a query string, and the Radix trigger that sets it cannot be driven in
 *  jsdom — so this is the only place the mapping from a chosen cohort to the sent query can be
 *  asserted for both cohorts. Built with `URLSearchParams` rather than concatenated: one of
 *  these endpoints already carries a param and the other does not, so `?` vs `&` is not the
 *  same by hand. Both call sites go through it; leaving one inline would put a second copy
 *  where a mutation could hide. */
export function extractPath(what: "hooks" | "structures", cohort: Cohort): string {
  // Annotated: without it the ternary widens to a union carrying `sample_size?: undefined`,
  // which `URLSearchParams` does not accept.
  const params: Record<string, string> =
    what === "structures" ? { sample_size: "27", cohort } : { cohort };
  return `/templates/extract/${what}?${new URLSearchParams(params)}`;
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

export default function TemplateManager({ initial }: { initial: Template[] }) {
  const router = useRouter();
  const [kind, setKind] = useState<TemplateKind>("hook");
  const [cohort, setCohort] = useState<Cohort>("voice");
  const [name, setName] = useState("");
  const [body, setBody] = useState(BLANK_BODY.hook);
  const [editing, setEditing] = useState<Template | null>(null);
  const [busy, setBusy] = useState(false);
  const [preview, setPreview] = useState<{ id: number; url: string } | null>(null);

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

  // Dresses the name input and the body textarea, now that the kind select draws its own border
  // from the same tokens. Retokenised with the select that left it: leaving `border-black/15`
  // here would put a differently-weighted border on the two controls directly under a
  // `border-border` trigger, which is the mismatch this slice exists to remove.
  const field = "w-full rounded-input border border-border bg-transparent px-3 py-2 text-meta";

  return (
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
          <span className="text-xs text-muted">
            Proposes patterns from the strongest posts in the chosen cohort. Nothing becomes
            usable until you approve it.
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
              Extraction reads the corpus and proposes hooks, structures and visuals — the two
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
                {t.provenance.length > 0 && (
                  <p className="mt-1 text-xs text-muted">
                    from {t.provenance.length} post{t.provenance.length === 1 ? "" : "s"}
                  </p>
                )}
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
  );
}
