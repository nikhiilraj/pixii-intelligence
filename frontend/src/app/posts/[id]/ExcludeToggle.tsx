"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { API_BASE } from "@/lib/api";

export default function ExcludeToggle({
  postId,
  excluded,
}: {
  postId: number;
  excluded: boolean;
}) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function toggle() {
    setBusy(true);
    setError(null);
    try {
      const res = await fetch(`${API_BASE}/posts/${postId}/exclude`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ excluded: !excluded }),
      });
      if (!res.ok) throw new Error(`failed (${res.status})`);
      router.refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mt-8 rounded-lg border border-black/10 p-3 dark:border-white/15">
      <p className="text-xs opacity-60">
        {excluded
          ? "Held out of extraction — templates are not learned from this post. It stays in the corpus and keeps its metrics."
          : "Teaching the generator from a post that won for reasons that cannot repeat — a launch, a network firing once — teaches a trick that only works once."}
      </p>
      <button
        onClick={toggle}
        disabled={busy}
        className="mt-3 rounded-md border border-black/20 px-3 py-1.5 text-sm disabled:opacity-50 dark:border-white/25"
      >
        {busy ? "Saving…" : excluded ? "Let back into extraction" : "Hold out of extraction"}
      </button>
      {error && <p className="mt-2 text-sm text-red-600 dark:text-red-400">{error}</p>}
    </div>
  );
}
