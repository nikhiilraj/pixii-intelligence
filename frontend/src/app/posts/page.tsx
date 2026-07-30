import { BackendUnreachable } from "@/components/backend-unreachable";
import { getJson, type Post, type Template } from "@/lib/api";

import AddExternal from "./AddExternal";
import Explorer from "./Explorer";

export const dynamic = "force-dynamic";

export default async function PostsPage() {
  const [posts, templates] = await Promise.all([
    getJson<Post[]>("/posts"),
    getJson<Template[]>("/templates"),
  ]);

  return (
    <main className="mx-auto max-w-6xl px-6 py-16">
      <h1 className="text-2xl font-semibold tracking-tight">Corpus</h1>
      <p className="mt-1 text-sm opacity-60">
        Every post, ranked by engaged actions — likes, comments, shares and saves. Reach and
        engagement rate sit alongside as secondary measures.
      </p>

      {posts === null ? (
        <BackendUnreachable className="mt-8" />
      ) : (
        <>
          <AddExternal />
          <Explorer initial={posts} templates={templates ?? []} />
        </>
      )}
    </main>
  );
}
