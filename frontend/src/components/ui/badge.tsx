import { cva, type VariantProps } from "class-variance-authority";

import { cn } from "@/lib/cn";

/* The status colours are FILLS, not text colours — measured, and this contradicts the PRD.
   `.scratch/v2/PRD.md` claims --success/--warning/--danger are "all AA on both surfaces".
   As text on light they are not:

     --success #2F9E44  3.29:1 on --bg   --warning #E8A33D  2.06:1   --danger #D6455D  4.12:1

   All three fail the 4.5:1 bar, warning badly. So a Badge tints the status colour behind a
   `--text` label instead of colouring the label: a 15% fill plus a 40% border reads as the
   status while the words stay at 13:1 or better in both themes. Same reasoning as US-001's
   --accent / --accent-text split, applied to status. */
const badgeVariants = cva(
  "inline-flex items-center rounded-input border px-1.5 py-0.5 text-caption font-medium",
  {
    variants: {
      variant: {
        neutral: "border-border bg-surface-2 text-muted",
        success: "border-success/40 bg-success/15 text-text",
        warning: "border-warning/40 bg-warning/15 text-text",
        danger: "border-danger/40 bg-danger/15 text-text",
      },
    },
    defaultVariants: { variant: "neutral" },
  },
);

export type BadgeProps = React.ComponentProps<"span"> & VariantProps<typeof badgeVariants>;

export function Badge({ className, variant, ...props }: BadgeProps) {
  return <span className={cn(badgeVariants({ variant }), className)} {...props} />;
}
