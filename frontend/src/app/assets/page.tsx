import { ApiFailureNotice } from "@/components/api-failure";
import { getJson, type Asset } from "@/lib/api";

import AssetLibrary from "./AssetLibrary";

export const dynamic = "force-dynamic";

export default async function AssetsPage() {
  const assets = await getJson<Asset[]>("/assets");

  return (
    <main className="mx-auto max-w-6xl px-6 py-16">
      <h1 className="text-title font-semibold tracking-tight">Assets</h1>
      <p className="mt-1 text-body text-muted">
        Logos, product shots and brand furniture, reusable across post visuals. An `image_url`
        slot on a visual template is filled from here — the model never writes one — and the
        renderer embeds the file rather than linking to it.
      </p>

      {assets.ok ? (
        <AssetLibrary initial={assets.data} />
      ) : (
        <ApiFailureNotice failure={assets} className="mt-8" />
      )}
    </main>
  );
}
