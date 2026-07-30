"use client";

import * as TooltipPrimitive from "@radix-ui/react-tooltip";

import { cn } from "@/lib/cn";

/* Radix Tooltip, styled onto the US-001 tokens. Vendored now because US-008 and US-014 need
 * it; nothing in the app shows a tooltip yet.
 *
 * `Tooltip` includes its own `Tooltip.Provider`, so a consumer mounts nothing globally and
 * cannot forget to. Radix throws without a provider in the tree, and a provider in
 * `layout.tsx` would be a client boundary around the whole app for a component no page uses
 * yet.
 * ponytail: one provider per tooltip means `skipDelayDuration` — the grace period where a
 * second tooltip in the same group opens instantly — is not shared between tooltips. Ceiling:
 * wrap a group in `TooltipPrimitive.Provider` directly if a dense row of icon buttons ever
 * makes that hesitation felt.
 *
 * A tooltip is never the only carrier of information. It is `role="tooltip"`, hidden from
 * touch entirely, and Radix's trigger requires a focusable child — so anything essential
 * belongs in an `aria-label` or visible text as well.
 */

export const TooltipTrigger = TooltipPrimitive.Trigger;

export function Tooltip({
  delayDuration = 200,
  ...props
}: React.ComponentProps<typeof TooltipPrimitive.Root>) {
  return (
    <TooltipPrimitive.Provider delayDuration={delayDuration}>
      <TooltipPrimitive.Root {...props} />
    </TooltipPrimitive.Provider>
  );
}

export function TooltipContent({
  className,
  sideOffset = 4,
  children,
  ...props
}: React.ComponentProps<typeof TooltipPrimitive.Content>) {
  return (
    <TooltipPrimitive.Portal>
      <TooltipPrimitive.Content
        sideOffset={sideOffset}
        className={cn(
          "z-50 max-w-xs rounded-input border border-border bg-surface px-2 py-1 text-caption text-text shadow-overlay",
          // 120ms: a tooltip is a micro interaction, not a structural one.
          "transition-opacity duration-[var(--duration-micro)] ease-standard starting:opacity-0",
          className,
        )}
        {...props}
      >
        {children}
        <TooltipPrimitive.Arrow className="fill-border" />
      </TooltipPrimitive.Content>
    </TooltipPrimitive.Portal>
  );
}
