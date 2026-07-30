import { cn } from "@/lib/cn";

/* Replaces `rounded-lg border border-black/10 p-3 dark:border-white/15`, pasted verbatim
   6 times. Depth is border-first per the PRD, so there is no shadow here — the one shadow
   token is reserved for overlays.

   ponytail: no cva. There is exactly one appearance today, so a variant table would be a
   table with one row. Callers that need a raised card add `bg-surface-2` (the brand cream)
   through className; if that becomes the common case, promote it to a `variant` prop then.
   Also no CardHeader/CardTitle/CardFooter subcomponents — nothing in the app has a card
   header, and shadcn's full family would be five unused exports. */
export function Card({ className, ...props }: React.ComponentProps<"div">) {
  return <div className={cn("rounded-card border border-border p-3", className)} {...props} />;
}
