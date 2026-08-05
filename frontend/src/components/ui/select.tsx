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
 * plain transition on `--duration-structural` and `--ease-standard`. It is therefore zeroed by
 * the `prefers-reduced-motion` block in globals.css, which overrides `transition-duration`
 * globally — a keyframe animation would need its own opt-out.
 *
 * Why not drive this off Radix's own `data-state`, which is the general rule: `data-state` can
 * animate neither end of this component's life. The content is portalled in *already carrying*
 * `data-state="open"`, so there is no state change for an enter transition to interpolate from
 * — that is exactly the gap `@starting-style` fills. And Radix's Presence gates unmount on
 * `animationend`, not `transitionend`, so `data-state="closed"` cannot drive an exit
 * transition either.
 * ponytail: so no exit animation. An exit would mean hand-written keyframes and the parallel
 * animation system the brief rules out. Closing is instant. Ceiling: add `@keyframes` in
 * globals.css keyed on `data-[state=closed]` if the snap ever reads as a glitch.
 * `data-state`/`data-side`/`data-highlighted` still drive every non-motion style here and in
 * tabs.tsx — the rule holds for state, it is transitions specifically that need the fallback.
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
        // The value truncates rather than widening the trigger. Auto-width triggers (the filter
        // rows on /posts and /assets) are unaffected — a max-content flex container ignores
        // `min-width: 0` when sizing itself — but a `w-full` trigger holding a long template name
        // would otherwise spill out of a fixed-width column, which at 390px is page overflow.
        // The icon gets `shrink-0` so the same rule cannot squeeze the chevron away.
        "[&>span]:min-w-0 [&>span]:truncate",
        className,
      )}
      {...props}
    >
      {children}
      <SelectPrimitive.Icon className="shrink-0 text-muted">
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
          "transition-[opacity,transform] duration-[var(--duration-structural)] ease-standard starting:scale-95 starting:opacity-0",
          className,
        )}
        {...props}
      >
        <SelectPrimitive.Viewport className="p-1">{children}</SelectPrimitive.Viewport>
      </SelectPrimitive.Content>
    </SelectPrimitive.Portal>
  );
}

/* A heading over a run of items, and the run it heads.
 *
 * `SelectPrimitive.Group` + `Label` rather than a disabled `SelectItem` styled to look like a
 * heading: a disabled item is still an option in the accessibility tree and Radix's typeahead
 * can still land on it, so a reader arrowing through would stop on a word that cannot be
 * chosen. `Group` renders `role="group"` and `Label` is wired to it by `aria-labelledby`, which
 * is the same relationship a native `<optgroup>` has.
 *
 * `pl-6` matches `SelectItem`'s, so the heading aligns with the item *text* rather than with
 * the check-indicator gutter. Every page's content being misaligned with the nav is one of the
 * three defects this project found by looking rather than by testing; a heading sitting six
 * pixels left of the words under it is the same mistake at a smaller scale.
 */
export const SelectGroup = SelectPrimitive.Group;

export function SelectLabel({
  className,
  ...props
}: React.ComponentProps<typeof SelectPrimitive.Label>) {
  return (
    <SelectPrimitive.Label
      className={cn(
        // `py-1.5 pr-2 pl-6`, mirroring `SelectItem`'s own `py-1 pr-2 pl-6`, rather than
        // `px-2 … pl-6`: the second sets padding-left twice and leaves which one wins to
        // whether `cn` merges Tailwind classes or merely concatenates them. No test in this
        // suite can see the result, which is exactly the kind of misalignment worth not
        // leaving to a library's merge order.
        "py-1.5 pr-2 pl-6 font-mono text-caption uppercase tracking-label text-muted",
        className,
      )}
      {...props}
    />
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
