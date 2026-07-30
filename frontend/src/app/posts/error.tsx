"use client";

import { RouteError } from "@/components/route-error";

export default function PostsError(props: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return <RouteError what="The corpus" {...props} />;
}
