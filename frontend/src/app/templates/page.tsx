import Link from "next/link";

import { ApiFailureNotice } from "@/components/api-failure";
import { getJson, type Template } from "@/lib/api";

import TemplateManager from "./TemplateManager";

export const dynamic = "force-dynamic";

export default async function TemplatesPage() {
  const templates = await getJson<Template[]>("/templates");

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
        <TemplateManager initial={templates.data} defaultMode="review" />
      ) : (
        <ApiFailureNotice failure={templates} className="mt-8" />
      )}
    </main>
  );
}
