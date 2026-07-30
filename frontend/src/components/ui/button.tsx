import { cva, type VariantProps } from "class-variance-authority";

import { cn } from "@/lib/cn";

/* The two class strings this replaces were pasted verbatim ~9 times across the app:

     outline:  rounded-md border border-black/20 px-3 py-1.5 text-sm disabled:opacity-50 dark:border-white/25
     primary:  rounded-md bg-black px-3 py-1.5 text-sm text-white disabled:opacity-50 dark:bg-white dark:text-black

   Two deliberate changes from those originals:

   1. `disabled:opacity-50` is gone. The PRD names opacity-as-muted as an AA failure —
      50% of `--text` on `--bg` lands around 3:1. Disabled now goes flat instead of faded:
      a `--surface-2` fill with a `--text-muted` label, measured at 5.2:1 light and 5.25:1
      dark, so it clears AA even though WCAG exempts inactive controls. (`--border` as the
      disabled fill was the first attempt and only reached 4.26:1 — worth measuring.)
   2. `bg-black`/`dark:bg-white` becomes `bg-text`/`text-bg`, which resolves to the same
      ink inversion through the tokens (16.8:1 light, 15.7:1 dark).

   There is deliberately NO accent-filled variant. `--accent-fg` (#ffffff) on `--accent`
   (#F2610C) measures 3.24:1 — it fails AA as text, so an orange primary button would
   reintroduce exactly the contrast bug US-001 created `--accent-text` to avoid. The accent
   stays on the focus ring and on borders. */
const buttonVariants = cva(
  // min-h-8 pins the 32px hit target from the PRD's Accessibility section so padding
  // changes cannot shrink it silently. No focus-visible classes: globals.css already
  // applies one 2px accent-text ring to every interactive element, and repeating it
  // per component double-draws the outline.
  // `border border-transparent` on the base so the disabled state can turn a border on
  // without the button growing 2px. Both variants therefore have identical box metrics.
  "inline-flex min-h-8 items-center justify-center gap-2 rounded-input border border-transparent px-3 py-1.5 text-meta font-medium transition-colors disabled:border-border disabled:bg-surface-2 disabled:text-muted",
  {
    variants: {
      variant: {
        primary: "bg-text text-bg hover:bg-text/90",
        outline: "border-border hover:bg-surface-2",
      },
    },
    defaultVariants: { variant: "primary" },
  },
);

export type ButtonProps = React.ComponentProps<"button"> &
  VariantProps<typeof buttonVariants>;

export function Button({ className, variant, ...props }: ButtonProps) {
  return <button className={cn(buttonVariants({ variant }), className)} {...props} />;
}
