"use client";

import * as SelectPrimitive from "@radix-ui/react-select";

import { cn } from "@/lib/cn";

/* Radix Select, styled onto the US-001 tokens.
 *
 * This is the component the dependency was justified for. A native `<select>` is accessible
 * but unstylable; a hand-rolled listbox needs `role="listbox"`/`aria-activedescendant`, a
 * focus trap, typeahead, Escape, outside-click, scroll lock and focus return — all of which
 * Radix already ships and tests upstream. None of that behaviour is re-implemented or
 * re-asserted here (see the frontend testing policy in progress.txt).
 *
 * Two deliberate constraints, both inherited:
 *
 * 1. No `focus-visible` classes. globals.css draws one 2px `--accent-text` ring on every
 *    interactive element; repeating it per component double-draws the outline (button.tsx
 *    records the same finding). The highlighted *item* is a different signal and uses
 *    `--surface-2` as a fill — never `--accent` behind text.
 * 2. `min-h-8` pins the PRD's 32px hit target on the trigger and on every item, so padding
 *    changes cannot shrink it silently.
 *
 * Motion is enter-only, via `@starting-style` (Tailwind v4's `starting:` variant) plus a
 * plain transition on the token duration and easing. It is therefore zeroed by the
 * `prefers-reduced-motion` block in globals.css, which overrides `transition-duration`
 * globally — a keyframe animation would need its own opt-out.
 * ponytail: no exit animation. Radix's Presence waits on `animationend`, not on a
 * transition, so an exit would mean hand-written keyframes and a parallel animation system
 * the brief rules out. Closing is instant. Ceiling: add `@keyframes` in globals.css keyed on
 * `data-[state=closed]` if the snap ever reads as a glitch.
 */

function ChevronDown() {
  return (
    <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true" fill="none">
      <path d="M3 4.5 6 7.5 9 4.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
    </svg>
  );
}

function Check() {
  return (
    <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true" fill="none">
      <path d="M2.5 6.5 5 9l4.5-6" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
    </svg>
  );
}

export const Select = SelectPrimitive.Root;
export const SelectValue = SelectPrimitive.Value;

export function SelectTrigger({
  className,
  children,
  ...props
}: React.ComponentProps<typeof SelectPrimitive.Trigger>) {
  return (
    <SelectPrimitive.Trigger
      className={cn(
        "inline-flex min-h-8 items-center justify-between gap-2 rounded-input border border-border bg-transparent px-2 py-1.5 text-meta transition-colors hover:bg-surface-2",
        "data-[placeholder]:text-muted data-[disabled]:bg-surface-2 data-[disabled]:text-muted",
        className,
      )}
      {...props}
    >
      {children}
      <SelectPrimitive.Icon className="text-muted">
        <ChevronDown />
      </SelectPrimitive.Icon>
    </SelectPrimitive.Trigger>
  );
}

export function SelectContent({
  className,
  children,
  position = "popper",
  sideOffset = 4,
  ...props
}: React.ComponentProps<typeof SelectPrimitive.Content>) {
  return (
    <SelectPrimitive.Portal>
      <SelectPrimitive.Content
        position={position}
        sideOffset={sideOffset}
        className={cn(
          "z-50 min-w-[var(--radix-select-trigger-width)] overflow-hidden rounded-card border border-border bg-surface text-meta shadow-overlay",
          // The list can be long — /templates carries 47 rows. Radix's Viewport is
          // `overflow: hidden auto`, so capping the height here is all that scrolling needs;
          // keyboard navigation scrolls the highlighted item into view on its own.
          // ponytail: no ScrollUpButton/ScrollDownButton — the wheel and the arrow keys both
          // already work, and the buttons only add a mouse affordance.
          "max-h-[min(20rem,var(--radix-select-content-available-height))]",
          "transition-[opacity,transform] duration-200 ease-standard starting:scale-95 starting:opacity-0",
          className,
        )}
        {...props}
      >
        <SelectPrimitive.Viewport className="p-1">{children}</SelectPrimitive.Viewport>
      </SelectPrimitive.Content>
    </SelectPrimitive.Portal>
  );
}

export function SelectItem({
  className,
  children,
  ...props
}: React.ComponentProps<typeof SelectPrimitive.Item>) {
  return (
    <SelectPrimitive.Item
      className={cn(
        // No `outline-none` here, which shadcn's version carries: globals.css's
        // `:focus-visible` rule is unlayered and beats `@layer utilities`, so the class would
        // be dead code contradicting this file's own comment. Radix moves real DOM focus to
        // the highlighted option, so it gets the accent ring on top of the `--surface-2` fill —
        // two signals for one state, which is correct rather than redundant.
        "relative flex min-h-8 cursor-default items-center gap-2 rounded-input py-1 pr-2 pl-6 select-none",
        "data-[highlighted]:bg-surface-2 data-[disabled]:text-muted",
        className,
      )}
      {...props}
    >
      <SelectPrimitive.ItemIndicator className="absolute left-1.5 text-accent-text">
        <Check />
      </SelectPrimitive.ItemIndicator>
      <SelectPrimitive.ItemText>{children}</SelectPrimitive.ItemText>
    </SelectPrimitive.Item>
  );
}
