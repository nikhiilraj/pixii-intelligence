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

      {templates === null ? (
        <p className="mt-8 text-sm text-red-600 dark:text-red-400">
          Backend unreachable. Start it with{" "}
          <code className="rounded bg-black/5 px-1 dark:bg-white/10">make api</code>.
        </p>
      ) : (
        <TemplateManager initial={templates} />
      )}
    </main>
  );
}
