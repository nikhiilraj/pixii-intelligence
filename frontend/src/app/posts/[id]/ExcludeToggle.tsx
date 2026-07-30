"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { postJson } from "@/lib/api";

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

    // This was the one call site that threw away the body entirely — `failed (404)` where
    // the API had written "no post 999". The helper carries the message, so it gets shown.
    const result = await postJson(`/posts/${postId}/exclude`, { excluded: !excluded });
    setBusy(false);

    if (result.ok) router.refresh();
    else setError(result.message);
  }

  return (
    <div className="mt-8 rounded-lg border border-black/10 p-3 dark:border-white/15">
      <p className="text-xs opacity-60">
        {excluded
          ? "Held out of extraction — templates are not learned from this post. It stays in the corpus and keeps its metrics."
          : "Teaching the generator from a post that won for reasons that cannot repeat — a launch, a network firing once — teaches a trick that only works once."}
      </p>
      <Button variant="outline" onClick={toggle} disabled={busy} className="mt-3">
        {busy ? "Saving…" : excluded ? "Let back into extraction" : "Hold out of extraction"}
      </Button>
      {error && <p className="mt-2 text-sm text-red-600 dark:text-red-400">{error}</p>}
    </div>
  );
}
