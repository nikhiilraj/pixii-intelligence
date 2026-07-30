"use client";

import * as DialogPrimitive from "@radix-ui/react-dialog";

import { cn } from "@/lib/cn";

/* Radix Dialog, styled onto the US-001 tokens. Vendored now because US-008 (asset delete)
 * and US-014 (template retire, push to Zernio) need the PRD's confirm dialogs; nothing in
 * the app opens one yet.
 *
 * `DialogContent` carries its own Portal and Overlay. That is not decoration: without the
 * portal the dialog renders inside whatever `overflow` container the trigger sits in — the
 * corpus table scrolls horizontally — and without the overlay there is no scroll lock. Both
 * belong to the component, not to the caller, so a consumer cannot forget them.
 *
 * Radix warns if `Dialog.Title` is missing, because the dialog would then have no accessible
 * name. Always render `DialogTitle`; use `DialogDescription` for the consequence the PRD
 * requires a destructive action to name.
 *
 * Motion, focus ring and hit targets follow select.tsx — enter-only transition on the token
 * duration, no per-component focus classes, `min-h-8` on the close button.
 * ponytail: no DialogHeader/DialogFooter wrappers. They are a `div` with two utility classes
 * and no behaviour; US-008 can lay its own out.
 */

function Close() {
  return (
    <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true" fill="none">
      <path d="M3 3l6 6M9 3l-6 6" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
    </svg>
  );
}

export const Dialog = DialogPrimitive.Root;
export const DialogTrigger = DialogPrimitive.Trigger;
export const DialogClose = DialogPrimitive.Close;

export function DialogContent({
  className,
  children,
  ...props
}: React.ComponentProps<typeof DialogPrimitive.Content>) {
  return (
    <DialogPrimitive.Portal>
      <DialogPrimitive.Overlay className="fixed inset-0 z-50 bg-black/40 transition-opacity duration-200 ease-standard starting:opacity-0" />
      <DialogPrimitive.Content
        className={cn(
          "fixed top-1/2 left-1/2 z-50 w-[calc(100%-3rem)] max-w-lg -translate-x-1/2 -translate-y-1/2 rounded-dialog border border-border bg-surface p-5 shadow-overlay",
          "transition-[opacity,transform] duration-200 ease-standard starting:scale-95 starting:opacity-0",
          className,
        )}
        {...props}
      >
        {children}
        <DialogPrimitive.Close
          aria-label="close"
          className="absolute top-3 right-3 inline-flex min-h-8 min-w-8 items-center justify-center rounded-input text-muted transition-colors hover:bg-surface-2 hover:text-text"
        >
          <Close />
        </DialogPrimitive.Close>
      </DialogPrimitive.Content>
    </DialogPrimitive.Portal>
  );
}

export function DialogTitle({
  className,
  ...props
}: React.ComponentProps<typeof DialogPrimitive.Title>) {
  return (
    <DialogPrimitive.Title className={cn("pr-8 text-head font-semibold", className)} {...props} />
  );
}

export function DialogDescription({
  className,
  ...props
}: React.ComponentProps<typeof DialogPrimitive.Description>) {
  return (
    <DialogPrimitive.Description
      className={cn("mt-2 text-body text-muted", className)}
      {...props}
    />
  );
}
