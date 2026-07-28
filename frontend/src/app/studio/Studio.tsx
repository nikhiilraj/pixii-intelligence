"use client";

import { useState } from "react";

import { API_BASE, type Draft, type Template } from "@/lib/api";

type Picked = { hook: number | null; structure: number | null; visual: number | null };

function Lineage({ draft }: { draft: Draft }) {
  const rows = [
    ["Hook", draft.lineage.hook],
    ["Structure", draft.lineage.structure],
    ["Visual", draft.lineage.visual],
  ] as const;

  return (
    <div className="rounded-lg border border-black/10 p-3 text-xs dark:border-white/15">
      <div className="mb-2 font-medium uppercase tracking-widest opacity-50">Lineage</div>
      {rows.map(([label, entry]) => (
        <div key={label} className="flex justify-between gap-3 py-0.5">
          <span className="opacity-60">{label}</span>
          <span>{entry ? `${entry.name} v${entry.version}` : "—"}</span>
        </div>
      ))}
    </div>
  );
}

export default function Studio({ templates }: { templates: Template[] }) {
  const [idea, setIdea] = useState("");
  const [picked, setPicked] = useState<Picked>({ hook: null, structure: null, visual: null });
  const [reason, setReason] = useState<string | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const approved = templates.filter((t) => t.status === "approved");
  const of = (kind: Template["kind"]) => approved.filter((t) => t.kind === kind);

  async function call<T>(path: string, body?: unknown): Promise<T | null> {
    setBusy(path);
    setError(null);
    try {
      const res = await fetch(`${API_BASE}${path}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: body === undefined ? undefined : JSON.stringify(body),
      });
      if (!res.ok) {
        const detail = await res.json().catch(() => ({}));
        throw new Error(detail.detail ?? `failed (${res.status})`);
      }
      return (await res.json()) as T;
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed");
      return null;
    } finally {
      setBusy(null);
    }
  }

  const payload = {
    idea,
    hook_id: picked.hook,
    structure_id: picked.structure,
    visual_id: picked.visual,
  };

  const field =
    "w-full rounded-md border border-black/15 bg-transparent px-3 py-2 text-sm dark:border-white/20";
  const button =
    "rounded-md border border-black/20 px-3 py-1.5 text-sm disabled:opacity-50 dark:border-white/25";

  return (
    <div className="mt-8 grid gap-10 lg:grid-cols-[22rem_1fr]">
      <section className="space-y-3">
        <textarea
          value={idea}
          onChange={(e) => setIdea(e.target.value)}
          rows={5}
          placeholder="An idea, a finding, a link — what is this post about?"
          className={field}
        />

        {(["hook", "structure", "visual"] as const).map((kind) => (
          <select
            key={kind}
            value={picked[kind] ?? ""}
            onChange={(e) =>
              setPicked({ ...picked, [kind]: e.target.value ? Number(e.target.value) : null })
            }
            className={field}
          >
            <option value="">{kind} — let it suggest</option>
            {of(kind).map((t) => (
              <option key={t.id} value={t.id}>
                {t.name} (v{t.version})
              </option>
            ))}
          </select>
        ))}

        <div className="flex flex-wrap gap-2">
          <button
            disabled={!idea.trim() || busy !== null}
            className={button}
            onClick={async () => {
              const s = await call<{
                hook: { id: number };
                structure: { id: number };
                visual: { id: number };
                reason: string;
              }>("/drafts/suggest", payload);
              if (s) {
                setPicked({ hook: s.hook.id, structure: s.structure.id, visual: s.visual.id });
                setReason(s.reason);
              }
            }}
          >
            Suggest templates
          </button>
          <button
            disabled={!idea.trim() || busy !== null}
            className="rounded-md bg-black px-3 py-1.5 text-sm text-white disabled:opacity-50 dark:bg-white dark:text-black"
            onClick={async () => {
              const d = await call<Draft>("/drafts", payload);
              if (d) setDraft(d);
            }}
          >
            {busy === "/drafts" ? "Writing…" : "Generate draft"}
          </button>
        </div>

        {reason && <p className="text-xs opacity-60">{reason}</p>}
        {error && <p className="text-sm text-red-600 dark:text-red-400">{error}</p>}
        {approved.length === 0 && (
          <p className="text-sm text-amber-700 dark:text-amber-400">
            Nothing approved yet. Approve a hook, a structure and a visual first.
          </p>
        )}
      </section>

      <section className="space-y-4">
        {!draft ? (
          <p className="text-sm opacity-50">No draft yet.</p>
        ) : (
          <>
            <Lineage draft={draft} />

            <article className="whitespace-pre-wrap rounded-lg border border-black/10 p-4 text-[15px] leading-relaxed dark:border-white/15">
              {draft.full_text}
            </article>

            {draft.visual_png ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img
                src={`data:image/png;base64,${draft.visual_png}`}
                alt="Generated visual"
                className="w-full max-w-sm rounded-lg border border-black/10 dark:border-white/15"
              />
            ) : (
              <p className="text-sm text-amber-700 dark:text-amber-400">
                Visual not produced: {draft.visual_error ?? "unknown"} — the text is unaffected.
              </p>
            )}

            <div className="flex gap-2">
              <button
                disabled={busy !== null}
                className={button}
                onClick={async () => {
                  const d = await call<Draft>(`/drafts/${draft.id}/regenerate-text`);
                  if (d) setDraft(d);
                }}
              >
                Rewrite text
              </button>
              <button
                disabled={busy !== null}
                className={button}
                onClick={async () => {
                  const d = await call<Draft>(`/drafts/${draft.id}/regenerate-visual`);
                  if (d) setDraft(d);
                }}
              >
                Redraw visual
              </button>
            </div>
          </>
        )}
      </section>
    </div>
  );
}
