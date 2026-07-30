import { ApiFailureNotice } from "@/components/api-failure";
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

      {posts.ok ? (
        <>
          <AddExternal />
          {/* The template list only populates the family filter, so a failure there does not
              cost the page its corpus — but it is reported rather than passed off as an empty
              list. `templates ?? []` used to make a failed read indistinguishable from an
              account with no templates, which is the bug this slice exists to remove. */}
          {!templates.ok && <ApiFailureNotice failure={templates} className="mt-4" />}
          <Explorer initial={posts.data} templates={templates.ok ? templates.data : []} />
        </>
      ) : (
        <ApiFailureNotice failure={posts} className="mt-8" />
      )}
    </main>
  );
}
