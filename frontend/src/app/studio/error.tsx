"use client";

import { RouteError } from "@/components/route-error";

/* A draft written in this session lives in Studio's local state, so a throw here loses it —
   which is why the copy says nothing was *written*, and does not say nothing was lost. */
export default function StudioError(props: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return <RouteError what="Studio" {...props} />;
}
