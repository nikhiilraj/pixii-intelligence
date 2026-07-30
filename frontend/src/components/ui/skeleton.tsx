import { cn } from "@/lib/cn";

/* No consumer in this slice. It exists because the PRD requires every route to ship a
   `loading.tsx` whose skeleton mirrors the real layout rather than a spinner, and adding
   a loading.tsx here purely to give this a call site would be scope creep — the route
   shells belong to the slice that redesigns them.

   ponytail: Tailwind's built-in `animate-pulse` (2s) rather than the PRD's 1.6s. Matching
   1.6s means overriding `--animate-pulse` in globals.css, which is US-001's file, for
   400ms nobody can see. Upgrade path: add `--animate-pulse: pulse 1.6s ...` to the @theme
   block when globals.css is next touched. `prefers-reduced-motion` already zeroes this
   globally, so there is nothing per-component to add for it. */
export function Skeleton({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      aria-hidden
      className={cn("animate-pulse rounded-input bg-surface-2", className)}
      {...props}
    />
  );
}
