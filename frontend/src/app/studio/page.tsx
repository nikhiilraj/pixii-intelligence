import { BackendUnreachable } from "@/components/backend-unreachable";
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
        <BackendUnreachable className="mt-8" />
      ) : (
        <Studio templates={templates} />
      )}
    </main>
  );
}
