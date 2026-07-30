"use client";

import { useRouter } from "next/navigation";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";

/* The body of every route's `error.tsx`. Each route ships a five-line file that names itself
   and delegates here, because the only thing that differs between them is that name.

   **What reaches this boundary, and what does not.** This is not where a failed API read
   lands. US-003 made `getJson` return `ApiResult` rather than throw, so a 500, a 422 and a
   dead backend are *values* a page renders `ApiFailureNotice` for — inside the page, beside
   whatever else loaded. That is the honest place for them, and it is also the only place the
   API's `detail` string exists: Next replaces a server component's error message with an
   opaque digest in a production build, so a page that threw in order to "surface detail in
   error.tsx" would read correctly under `pnpm dev` and degrade to generic copy once built.
   The `detail` copy therefore stays in `api-failure.tsx`, and this boundary catches what is
   left — a render that threw. A bad date, an unexpected shape, a bug.

   So the copy here promises nothing about a server: it says something threw, says nothing was
   written, and offers the retry. `error.message` is printed when there is one and the digest
   when there is not, which is what a production build will actually hand over.

   ponytail: `router.refresh()` alongside `reset()`, not a retry counter, not backoff, not a
   report-to-somewhere hook. `reset()` re-renders this segment against the RSC payload it
   already has, so on a server-component route it can reproduce the same failure forever;
   `refresh()` is what re-requests. Ceiling: if a route ever needs to distinguish "retried and
   failed again" from "first failure", count in this component — nothing needs it yet. */
export function RouteError({
  what,
  error,
  reset,
}: {
  what: string;
  error: Error & { digest?: string };
  reset: () => void;
}) {
  const router = useRouter();

  return (
    <main className="mx-auto max-w-3xl px-6 py-16">
      <h1 className="text-title font-semibold tracking-tight">{what} could not be shown</h1>
      <p className="mt-1 max-w-2xl text-body text-muted">
        Something on this page threw instead of rendering. Nothing was written — this is a read
        that broke, so trying again is safe.
      </p>

      {/* The danger colour tints the card and its border; the words stay --text. #D6455D is
          4.12:1 on --bg and fails AA as text (see badge.tsx). */}
      <Card role="alert" className="mt-6 border-danger/40 bg-danger/10 text-body">
        <p className="font-medium">
          {error.message || "The error arrived without a message."}
        </p>
        {error.digest && (
          <p className="mt-1 font-mono text-caption text-muted">digest {error.digest}</p>
        )}
      </Card>

      <Button
        className="mt-6"
        onClick={() => {
          router.refresh();
          reset();
        }}
      >
        Try again
      </Button>
    </main>
  );
}
