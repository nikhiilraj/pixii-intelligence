"use client";

import { useState } from "react";

import { Button } from "@/components/ui/button";
import { postBlob } from "@/lib/api";

/**
 * Renders what is in the editor, not what is saved.
 *
 * The saved-template preview already existed (`TemplateManager`'s `renderPreview`, which posts
 * to `/templates/{id}/preview`); this one exists because the thing an author needs to see while
 * hand-writing a layout is the edit they have not committed yet, and that edit has no id to post
 * to. Slot examples are the values, for the same reason the saved preview uses them: they are
 * what the author wrote down as representative.
 */
export default function TemplatePreview({ body, slots }: { body: string; slots: unknown[] }) {
  const [url, setUrl] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function preview() {
    let parsed: Record<string, unknown>;
    try {
      parsed = JSON.parse(body);
    } catch {
      // Reported inline rather than as a toast: this is the one control on the page that reads
      // the textarea directly, so the failure belongs beside the field that caused it.
      setProblem("Body is not valid JSON.");
      return;
    }

    const values = Object.fromEntries(
      (slots as Record<string, unknown>[]).map((slot) => [
        String(slot.name),
        String(slot.example ?? slot.name ?? ""),
      ]),
    );

    setBusy(true);
    setProblem(null);
    // `POST /templates/preview` — the unsaved twin of `/templates/{id}/preview` — renders `html`
    // only and answers 501 for `ai`, since a body with no id and no rate limit reaching
    // `AzureImageRenderer` from a textarea keystroke is a paid generation nobody asked for.
    // `result.message` carries that detail verbatim rather than a generic failure string.
    const result = await postBlob("/templates/preview", { body: parsed, slots, values });
    setBusy(false);

    if (result.ok) setUrl(URL.createObjectURL(result.data));
    else setProblem(result.message);
  }

  return (
    <div className="mt-4">
      <Button type="button" variant="outline" onClick={() => void preview()} disabled={busy}>
        {busy ? "Rendering…" : "Preview"}
      </Button>
      {problem && <p className="mt-2 text-meta text-muted">{problem}</p>}
      {/* eslint-disable-next-line @next/next/no-img-element -- a blob: URL from the render,
          not an asset next/image can optimise. */}
      {url && <img src={url} alt="Template preview" className="mt-3 w-full max-w-sm rounded-input border border-border" />}
    </div>
  );
}
