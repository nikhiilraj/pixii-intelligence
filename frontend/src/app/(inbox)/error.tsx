"use client";

import { RouteError } from "@/components/route-error";

/* Also the boundary for any child segment that has none of its own — every one of them ships
   one, so in practice this catches the Inbox only. */
export default function InboxError(props: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return <RouteError what="The Inbox" {...props} />;
}
