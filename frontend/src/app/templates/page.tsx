import Link from "next/link";

import { ApiFailureNotice } from "@/components/api-failure";
import { getJson, type Template, type UncoveredCounts } from "@/lib/api";

import TemplateManager from "./TemplateManager";

export const dynamic = "force-dynamic";

export default async function TemplatesPage() {
  /* Two reads, and only the first can empty the page. A failed `/templates/uncovered` is passed
     through as `undefined`, which renders `—` beside the extract buttons — the count was never
     collected, so no number is claimed — while the library itself still lists and reviews. The
     count is read here rather than in the client component because `send` already calls
     `router.refresh()`, so it falls after an extract run with no polling of its own. */
  const [templates, uncovered] = await Promise.all([
    getJson<Template[]>("/templates"),
    getJson<UncoveredCounts>("/templates/uncovered"),
  ]);

  return (
    <main className="mx-auto max-w-6xl px-6 py-16">
      <p className="text-caption font-medium uppercase tracking-[0.18em] text-muted">System memory</p>
      <h1 className="mt-2 text-display font-semibold tracking-[-0.04em]">Templates</h1>
      <p className="mt-2 max-w-2xl text-body text-muted">
        Review proposed hooks, structures and visuals one decision at a time. Editing creates a
        new version, so evidence stays attached to the wording that earned it. See the{" "}
        {/* US-018: the Scoreboard's only inbound link, now that nav is five destinations. It
            belongs here rather than in the nav — it reads `/metrics/templates`, so it is this
            library's evidence, and `scoreboard/page.tsx` already links back to this page from
            its empty state. Deleting this link orphans that route. */}
        <Link href="/scoreboard" className="underline">
          evidence scoreboard
        </Link>
        .
      </p>

      {templates.ok ? (
        <TemplateManager
          initial={templates.data}
          uncovered={uncovered.ok ? uncovered.data : undefined}
          defaultMode="review"
        />
      ) : (
        <ApiFailureNotice failure={templates} className="mt-8" />
      )}
    </main>
  );
}
