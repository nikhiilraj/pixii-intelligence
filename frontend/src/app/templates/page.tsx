import Link from "next/link";

import { ApiFailureNotice } from "@/components/api-failure";
import { getJson, type Template } from "@/lib/api";

import TemplateManager from "./TemplateManager";

export const dynamic = "force-dynamic";

export default async function TemplatesPage() {
  const templates = await getJson<Template[]>("/templates");

  return (
    <main className="mx-auto max-w-6xl px-6 py-16">
      <h1 className="text-title font-semibold tracking-tight">Templates</h1>
      <p className="mt-1 max-w-2xl text-body text-muted">
        Hooks, structures and visuals. Editing writes a new version, so performance stays
        attached to the wording that earned it. What each version has actually done is on the{" "}
        {/* US-018: the Scoreboard's only inbound link, now that nav is five destinations. It
            belongs here rather than in the nav — it reads `/metrics/templates`, so it is this
            library's evidence, and `scoreboard/page.tsx` already links back to this page from
            its empty state. Deleting this link orphans that route. */}
        <Link href="/scoreboard" className="underline">
          Scoreboard
        </Link>
        .
      </p>

      {templates.ok ? (
        <TemplateManager initial={templates.data} />
      ) : (
        <ApiFailureNotice failure={templates} className="mt-8" />
      )}
    </main>
  );
}
