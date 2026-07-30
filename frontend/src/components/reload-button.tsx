"use client";

import { Button } from "@/components/ui/button";

/* The retry beside a failed read on a page that renders on the server.
 *
 * Every read in this app is a `cache: "no-store"` fetch inside an async server component on a
 * `force-dynamic` route, so there is no client-side request to re-issue and nothing to re-run
 * in place — the retry has to make the server render the route again.
 *
 * ponytail: `window.location.reload()`, not `useRouter().refresh()`. Both re-request; reload is
 * the one that needs no router context, which keeps this component renderable from a plain
 * `render()` in a test and keeps the failure path free of a hook that throws outside an App
 * Router tree. The cost is that in-page client state is discarded — and on a page whose server
 * read just failed there is no state worth keeping, because nothing rendered. Ceiling: swap to
 * `router.refresh()` the day a failed read sits beside a form somebody has typed into.
 *
 * Nothing here is a retry *loop*: one click, one request, and the same notice comes back if it
 * fails again. Automatic retry would hide a backend that is down behind a page that looks busy.
 */
export function ReloadButton({ className }: { className?: string }) {
  return (
    <Button variant="outline" className={className} onClick={() => window.location.reload()}>
      Try again
    </Button>
  );
}
