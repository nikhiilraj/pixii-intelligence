"use client";

import { RouteError } from "@/components/route-error";

/* A missing post is not this boundary's business: `page.tsx` calls `notFound()` on a real 404,
   which routes to the not-found UI rather than here. This is for a post that exists and still
   would not render. */
export default function PostDetailError(props: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return <RouteError what="This post" {...props} />;
}
