import { clsx, type ClassValue } from "clsx";
import { extendTailwindMerge } from "tailwind-merge";

/* tailwind-merge only knows Tailwind's OWN class names. US-001's `@theme` added custom
   utilities whose names it cannot classify, and the `text-*` namespace is where that
   actually bites: `text-body`/`text-meta`/… are font sizes while `text-muted`/`text-danger`
   are colours, and tailwind-merge lumps every unrecognised `text-*` into one group.
   Measured before writing this — plain `twMerge("text-body text-muted")` returns
   `"text-muted"`, silently dropping the size. Registering the size names splits the group:
   it now returns both, while `"text-body text-head"` still collapses to `"text-head"`.
   The radii are here for the same reason (`rounded-input rounded-card` did not merge).

   Keep these lists in sync with the `@theme` blocks in globals.css. */
const twMerge = extendTailwindMerge({
  extend: {
    classGroups: {
      "font-size": [
        "text-caption",
        "text-meta",
        "text-body",
        "text-head",
        "text-title",
        "text-display",
      ],
      rounded: ["rounded-input", "rounded-card", "rounded-dialog"],
    },
  },
});

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}
