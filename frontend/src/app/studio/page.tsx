import { ApiFailureNotice } from "@/components/api-failure";
import { getJson, type Asset, type Template } from "@/lib/api";

import Studio from "./Studio";

export const dynamic = "force-dynamic";

export default async function StudioPage() {
  // The library is read here so the picker has something to offer. A failed read is handed on
  // as `null` rather than as `[]`: only the templates are load-bearing enough to replace the
  // whole page, and "the library is empty" is a claim a failed request cannot support.
  const [templates, assets] = await Promise.all([
    getJson<Template[]>("/templates"),
    getJson<Asset[]>("/assets"),
  ]);

  return (
    <main className="mx-auto max-w-6xl px-6 py-16">
      <h1 className="text-2xl font-semibold tracking-tight">Studio</h1>
      <p className="mt-1 text-sm opacity-60">
        An idea in, a reviewable draft out — stamped with the templates that produced it.
        Nothing publishes from here.
      </p>
      {templates.ok ? (
        <Studio templates={templates.data} assets={assets.ok ? assets.data : null} />
      ) : (
        <ApiFailureNotice failure={templates} className="mt-8" />
      )}
    </main>
  );
}
