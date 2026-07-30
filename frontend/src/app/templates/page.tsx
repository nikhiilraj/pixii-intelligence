import { ApiFailureNotice } from "@/components/api-failure";
import { getJson, type Template } from "@/lib/api";

import TemplateManager from "./TemplateManager";

export const dynamic = "force-dynamic";

export default async function TemplatesPage() {
  const templates = await getJson<Template[]>("/templates");

  return (
    <main className="mx-auto max-w-5xl px-6 py-16">
      <h1 className="text-2xl font-semibold tracking-tight">Templates</h1>
      <p className="mt-1 text-sm opacity-60">
        Hooks, structures and visuals. Editing writes a new version, so performance stays
        attached to the wording that earned it.
      </p>

      {templates.ok ? (
        <TemplateManager initial={templates.data} />
      ) : (
        <ApiFailureNotice failure={templates} className="mt-8" />
      )}
    </main>
  );
}
