import { getJson, type Template } from "@/lib/api";

import Studio from "./Studio";

export const dynamic = "force-dynamic";

export default async function StudioPage() {
  const templates = await getJson<Template[]>("/templates");

  return (
    <main className="mx-auto max-w-6xl px-6 py-16">
      <h1 className="text-2xl font-semibold tracking-tight">Studio</h1>
      <p className="mt-1 text-sm opacity-60">
        An idea in, a reviewable draft out — stamped with the templates that produced it.
        Nothing publishes from here.
      </p>
      {templates === null ? (
        <p className="mt-8 text-sm text-red-600 dark:text-red-400">
          Backend unreachable. Start it with{" "}
          <code className="rounded bg-black/5 px-1 dark:bg-white/10">make api</code>.
        </p>
      ) : (
        <Studio templates={templates} />
      )}
    </main>
  );
}
