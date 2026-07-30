"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
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

const BLANK_BODY: Record<TemplateKind, string> = {
  hook: JSON.stringify({ pattern: "{value} turned into {outcome}", tone: "direct" }, null, 2),
  structure: JSON.stringify(
    { sections: [{ name: "open", guidance: "" }], compatible_hooks: [] },
    null,
    2,
  ),
  visual: JSON.stringify({ renderer: "html", component: "stat_hero" }, null, 2),
};

const STATUS_STYLE: Record<string, string> = {
  approved: "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300",
  proposed: "bg-amber-500/15 text-amber-700 dark:text-amber-300",
  retired: "bg-black/10 opacity-60 dark:bg-white/10",
};

function Badge({ status }: { status: string }) {
  return (
    <span className={`rounded px-1.5 py-0.5 text-xs ${STATUS_STYLE[status] ?? ""}`}>{status}</span>
  );
}

/** Whose posts a template was read from.
 *
 * While reviewing a proposal this is the difference between a shape proven in our own
 * posts and one borrowed from a creator.
 *
 * Three states, three renderings. Most of the queue predates the field, and rendering
 * nothing for those would put a blank beside rows explicitly marked as borrowed — which
 * reads as "not borrowed", a claim the row does not carry. Unrecorded is the true one.
 */
function CohortTag({ template }: { template: Template }) {
  const cohort = cohortOf(template);
  if (!cohort) {
    return <span className="rounded px-1.5 py-0.5 text-xs opacity-40">cohort unrecorded</span>;
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

  const field =
    "w-full rounded-md border border-black/15 bg-transparent px-3 py-2 text-sm dark:border-white/20";

  return (
    <div className="mt-8 grid gap-10 lg:grid-cols-[1fr_20rem]">
      <section>
        <div className="mb-4 flex flex-wrap items-center gap-3">
          <select
            value={cohort}
            onChange={(e) => setCohort(e.target.value as Cohort)}
            disabled={busy}
            className="rounded-md border border-black/15 bg-transparent px-2 py-1.5 text-sm dark:border-white/20"
            aria-label="extraction cohort"
          >
            {COHORTS.map((c) => (
              <option key={c.value} value={c.value}>
                {c.label}
              </option>
            ))}
          </select>
          <Button
            variant="outline"
            onClick={() => send(`/templates/extract/hooks?${new URLSearchParams({ cohort })}`)}
            disabled={busy}
          >
            {busy ? "Working…" : "Extract hooks from top posts"}
          </Button>
          <Button
            variant="outline"
            onClick={() =>
              // Query string built rather than concatenated: this endpoint already carries
              // a param and the other does not, so `?` vs `&` is not the same by hand.
              send(
                `/templates/extract/structures?${new URLSearchParams({
                  sample_size: "27",
                  cohort,
                })}`,
              )
            }
            disabled={busy}
          >
            Extract structures
          </Button>
          <span className="text-xs opacity-50">
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
                  <span className="text-xs opacity-50">
                    {t.kind} · v{t.version}
                  </span>
                  <Badge status={t.status} />
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
                  <p className="mt-1 text-xs opacity-50">
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
        <h2 className="text-xs font-medium uppercase tracking-widest opacity-50">
          {editing ? `Revise "${editing.name}" → v${editing.version + 1}` : "Author a template"}
        </h2>

        {!editing && (
          <select
            value={kind}
            onChange={(e) => {
              const next = e.target.value as TemplateKind;
              setKind(next);
              setBody(BLANK_BODY[next]);
            }}
            className={field}
          >
            {KINDS.map((k) => (
              <option key={k} value={k}>
                {k}
              </option>
            ))}
          </select>
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
