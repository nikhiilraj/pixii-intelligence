"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { API_BASE, type Template, type TemplateKind } from "@/lib/api";

const KINDS: TemplateKind[] = ["hook", "structure", "visual"];

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

export default function TemplateManager({ initial }: { initial: Template[] }) {
  const router = useRouter();
  const [kind, setKind] = useState<TemplateKind>("hook");
  const [name, setName] = useState("");
  const [body, setBody] = useState(BLANK_BODY.hook);
  const [editing, setEditing] = useState<Template | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function send(path: string, init: RequestInit) {
    setBusy(true);
    setError(null);
    try {
      const res = await fetch(`${API_BASE}${path}`, {
        headers: { "Content-Type": "application/json" },
        ...init,
      });
      if (!res.ok) {
        const detail = await res.json().catch(() => ({}));
        throw new Error(detail.detail ?? `request failed (${res.status})`);
      }
      router.refresh();
      return true;
    } catch (e) {
      setError(e instanceof Error ? e.message : "request failed");
      return false;
    } finally {
      setBusy(false);
    }
  }

  function parseBody(): unknown | null {
    try {
      return JSON.parse(body);
    } catch {
      setError("Body is not valid JSON.");
      return null;
    }
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    const parsed = parseBody();
    if (parsed === null) return;

    const ok = editing
      ? await send(`/templates/${editing.id}`, {
          method: "PUT",
          body: JSON.stringify({ name, body: parsed }),
        })
      : await send("/templates", {
          method: "POST",
          body: JSON.stringify({ kind, name, body: parsed }),
        });

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
        <div className="mb-4 flex items-center gap-3">
          <button
            onClick={() => send("/templates/extract/hooks", { method: "POST" })}
            disabled={busy}
            className="rounded-md border border-black/20 px-3 py-1.5 text-sm disabled:opacity-50 dark:border-white/25"
          >
            {busy ? "Working…" : "Extract hooks from top posts"}
          </button>
          <span className="text-xs opacity-50">
            Proposes patterns from the strongest posts. Nothing becomes usable until you
            approve it.
          </span>
        </div>

        {initial.length === 0 ? (
          <p className="text-sm opacity-60">
            No templates yet. Author one, or run hook extraction once it exists.
          </p>
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
                        onClick={() => send(`/templates/${t.id}/approve`, { method: "POST" })}
                        disabled={busy}
                        className="underline opacity-70 hover:opacity-100"
                      >
                        approve
                      </button>
                    )}
                    {t.status !== "retired" && (
                      <button
                        onClick={() => send(`/templates/${t.id}/retire`, { method: "POST" })}
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

        {error && <p className="text-sm text-red-600 dark:text-red-400">{error}</p>}

        <div className="flex gap-3">
          <button
            type="submit"
            disabled={busy}
            className="rounded-md bg-black px-3 py-2 text-sm text-white disabled:opacity-50 dark:bg-white dark:text-black"
          >
            {editing ? "Save as new version" : "Create"}
          </button>
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
