import { ApiFailureNotice } from "@/components/api-failure";
import { getJson, type Asset } from "@/lib/api";

import AssetLibrary from "./AssetLibrary";

export const dynamic = "force-dynamic";

export default async function AssetsPage() {
  const assets = await getJson<Asset[]>("/assets");

  return (
    <main className="mx-auto max-w-6xl px-6 py-16">
      <p className="text-caption font-medium uppercase tracking-[0.18em] text-muted">Visual library</p>
      <h1 className="mt-2 text-display font-semibold tracking-[-0.04em]">Assets</h1>
      <p className="mt-2 max-w-2xl text-body text-muted">
        Keep reusable logos, product shots and brand furniture ready for Studio. Visual
        templates choose a slot; this library supplies the real file and keeps it local.
      </p>

      {assets.ok ? (
        <AssetLibrary initial={assets.data} />
      ) : (
        <ApiFailureNotice failure={assets} className="mt-8" />
      )}
    </main>
  );
}
