import { cn } from "@/lib/cn";

/* No consumer in this slice. It exists because the PRD requires every route to ship a
   `loading.tsx` whose skeleton mirrors the real layout rather than a spinner, and adding
   a loading.tsx here purely to give this a call site would be scope creep — the route
   shells belong to the slice that redesigns them.

   `animate-pulse` now runs at the PRD's 1.6s: US-017 took the upgrade path this comment
   used to name and overrode `--animate-pulse` in globals.css's @theme block, so the utility
   keeps Tailwind's `pulse` keyframe and only the timing moved. `prefers-reduced-motion` zeroes
   it from that same file, so there is nothing per-component to add for it. */
export function Skeleton({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      aria-hidden
      className={cn("animate-pulse rounded-input bg-surface-2", className)}
      {...props}
    />
  );
}
