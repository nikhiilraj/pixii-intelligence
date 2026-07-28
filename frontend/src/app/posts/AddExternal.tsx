"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { API_BASE } from "@/lib/api";

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
    try {
      const res = await fetch(`${API_BASE}/corpus/manual`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          content,
          author: author || null,
          engaged_actions: Number(engaged) || 0,
        }),
      });
      if (!res.ok) {
        const detail = await res.json().catch(() => ({}));
        throw new Error(detail.detail ?? `failed (${res.status})`);
      }
      setContent("");
      setAuthor("");
      setEngaged("");
      setOpen(false);
      router.refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed");
    } finally {
      setBusy(false);
    }
  }

  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        className="mt-4 rounded-md border border-black/20 px-3 py-1.5 text-sm dark:border-white/25"
      >
        Add a post from elsewhere
      </button>
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
        <button
          type="submit"
          disabled={busy || !content.trim()}
          className="rounded-md bg-black px-3 py-1.5 text-sm text-white disabled:opacity-50 dark:bg-white dark:text-black"
        >
          {busy ? "Adding…" : "Add to corpus"}
        </button>
        <button type="button" onClick={() => setOpen(false)} className="text-sm underline opacity-70">
          cancel
        </button>
      </div>
    </form>
  );
}
