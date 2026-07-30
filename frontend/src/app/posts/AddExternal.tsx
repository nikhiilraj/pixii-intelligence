"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { postJson } from "@/lib/api";

export default function AddExternal() {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [content, setContent] = useState("");
  const [author, setAuthor] = useState("");
  const [engaged, setEngaged] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const field =
    "w-full rounded-md border border-black/15 bg-transparent px-3 py-2 text-sm dark:border-white/20";

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);

    const result = await postJson<unknown>("/corpus/manual", {
      content,
      author: author || null,
      engaged_actions: Number(engaged) || 0,
    });
    setBusy(false);

    if (!result.ok) {
      setError(result.message);
      return;
    }

    setContent("");
    setAuthor("");
    setEngaged("");
    setOpen(false);
    router.refresh();
  }

  if (!open) {
    return (
      <Button variant="outline" onClick={() => setOpen(true)} className="mt-4">
        Add a post from elsewhere
      </Button>
    );
  }

  return (
    <form onSubmit={submit} className="mt-4 max-w-2xl space-y-3">
      <p className="text-xs opacity-60">
        For posts Zernio does not carry — a creator post someone sent you, text from a
        screenshot. Extraction treats it as evidence; a Zernio sync never touches it.
      </p>
      <textarea
        value={content}
        onChange={(e) => setContent(e.target.value)}
        rows={7}
        required
        placeholder="Paste the post text…"
        className={field}
      />
      <div className="flex gap-2">
        <input
          value={author}
          onChange={(e) => setAuthor(e.target.value)}
          placeholder="who wrote it (optional)"
          className={field}
        />
        <input
          value={engaged}
          onChange={(e) => setEngaged(e.target.value)}
          inputMode="numeric"
          placeholder="engaged actions"
          className={field}
        />
      </div>
      {error && <p className="text-sm text-red-600 dark:text-red-400">{error}</p>}
      <div className="flex gap-3">
        <Button type="submit" disabled={busy || !content.trim()}>
          {busy ? "Adding…" : "Add to corpus"}
        </Button>
        <button type="button" onClick={() => setOpen(false)} className="text-sm underline opacity-70">
          cancel
        </button>
      </div>
    </form>
  );
}
