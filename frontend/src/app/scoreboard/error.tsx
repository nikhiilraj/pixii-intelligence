"use client";

import { RouteError } from "@/components/route-error";

export default function ScoreboardError(props: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return <RouteError what="The scoreboard" {...props} />;
}
