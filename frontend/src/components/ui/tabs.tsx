"use client";

import * as TabsPrimitive from "@radix-ui/react-tabs";

import { cn } from "@/lib/cn";

/* Radix Tabs, styled onto the US-001 tokens. Vendored now because US-014 needs it; nothing
 * in the app renders tabs yet.
 *
 * What Radix carries here is the roving-tabindex `role="tablist"` contract — one tab stop for
 * the whole list, arrow keys to move between tabs, `aria-controls`/`aria-selected` wired to
 * the panels, and Home/End. A hand-rolled version of that is the usual source of a tab strip
 * that needs six Tab presses to get past.
 *
 * The active tab is marked with an `--accent` bottom border. That is a fill-like use of the
 * brand orange and is correct; the label itself stays on `--text` (US-001: never `--accent`
 * on text). No per-component focus classes — globals.css draws the one ring.
 */

export const Tabs = TabsPrimitive.Root;

export function TabsList({
  className,
  ...props
}: React.ComponentProps<typeof TabsPrimitive.List>) {
  return (
    <TabsPrimitive.List
      className={cn("flex items-center gap-1 border-b border-border", className)}
      {...props}
    />
  );
}

export function TabsTrigger({
  className,
  ...props
}: React.ComponentProps<typeof TabsPrimitive.Trigger>) {
  return (
    <TabsPrimitive.Trigger
      className={cn(
        // -mb-px so the active tab's 2px border sits on top of the list's 1px border
        // instead of below it.
        "-mb-px inline-flex min-h-8 items-center border-b-2 border-transparent px-3 py-1.5 text-meta font-medium text-muted transition-colors",
        "hover:text-text data-[state=active]:border-accent data-[state=active]:text-text",
        className,
      )}
      {...props}
    />
  );
}

export function TabsContent({
  className,
  ...props
}: React.ComponentProps<typeof TabsPrimitive.Content>) {
  return <TabsPrimitive.Content className={cn("mt-4", className)} {...props} />;
}
