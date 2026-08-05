"use client";

import { RouteError } from "@/components/route-error";

export default function OperationsError(props: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return <RouteError what="Operations" {...props} />;
}
