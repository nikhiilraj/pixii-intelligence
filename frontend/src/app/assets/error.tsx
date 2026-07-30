"use client";

import { RouteError } from "@/components/route-error";

export default function AssetsError(props: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return <RouteError what="The asset library" {...props} />;
}
