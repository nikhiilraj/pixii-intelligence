"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";

import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";

const ACTIONS = [
  { label: "Open Inbox", detail: "See what needs attention", href: "/", group: "Go" },
  { label: "Write a draft", detail: "Start in Studio", href: "/studio", group: "Do" },
  { label: "Review template proposals", detail: "Approve, reject or edit", href: "/templates", group: "Do" },
  { label: "Browse the corpus", detail: "Read published evidence", href: "/posts", group: "Go" },
  { label: "Open asset library", detail: "Upload or reuse visual material", href: "/assets", group: "Go" },
  { label: "View template evidence", detail: "Open the scoreboard", href: "/scoreboard", group: "Go" },
  // "Go", not "Do": this opens the screen. Every action on it is behind its own confirmation,
  // and a palette entry that fired a paid external call from a keystroke would be the exact
  // one-press mistake those confirmations exist to prevent.
  { label: "Open operations", detail: "Sync, ingest, generate a batch, browse research", href: "/operations", group: "Go" },
] as const;

export function CommandPalette() {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const input = useRef<HTMLInputElement>(null);
  const matches = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return needle
      ? ACTIONS.filter((action) => `${action.label} ${action.detail}`.toLowerCase().includes(needle))
      : ACTIONS;
  }, [query]);

  useEffect(() => {
    function shortcut(event: KeyboardEvent) {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setOpen((value) => !value);
      }
    }
    window.addEventListener("keydown", shortcut);
    return () => window.removeEventListener("keydown", shortcut);
  }, []);

  function choose(href: string) {
    setOpen(false);
    setQuery("");
    router.push(href);
  }

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="ml-auto flex min-h-8 shrink-0 items-center gap-2 rounded-input border border-border px-2.5 font-mono text-caption text-muted transition-colors hover:bg-surface-2 hover:text-text"
        aria-label="Open command menu"
      >
        <span className="hidden md:inline">Jump or do</span><kbd>⌘ K</kbd>
      </button>
      <Dialog
        open={open}
        onOpenChange={(next) => {
          setOpen(next);
          if (next) requestAnimationFrame(() => input.current?.focus());
          else setQuery("");
        }}
      >
        <DialogContent className="overflow-hidden p-0" onOpenAutoFocus={(event) => event.preventDefault()}>
          <DialogTitle className="sr-only">Jump or do</DialogTitle>
          <DialogDescription className="sr-only">Search actions and destinations.</DialogDescription>
          <div className="border-b border-border p-3">
            <input
              ref={input}
              value={query}
              onChange={(event) => { setQuery(event.target.value); setActive(0); }}
              onKeyDown={(event) => {
                if (event.key === "ArrowDown") { event.preventDefault(); setActive((value) => Math.min(value + 1, matches.length - 1)); }
                if (event.key === "ArrowUp") { event.preventDefault(); setActive((value) => Math.max(value - 1, 0)); }
                if (event.key === "Enter" && matches[active]) { event.preventDefault(); choose(matches[active].href); }
              }}
              placeholder="Search actions and pages…"
              aria-label="Search commands"
              className="min-h-10 w-full bg-transparent pr-10 text-body outline-none placeholder:text-muted"
            />
          </div>
          <div className="max-h-80 overflow-y-auto p-2">
            {matches.length ? matches.map((action, index) => (
              <button
                key={action.href}
                type="button"
                onMouseEnter={() => setActive(index)}
                onClick={() => choose(action.href)}
                className={`flex w-full items-center gap-4 rounded-input px-3 py-2.5 text-left ${active === index ? "bg-surface-2" : ""}`}
              >
                <span className="w-6 shrink-0 font-mono text-caption uppercase text-muted">{action.group}</span>
                <span className="min-w-0"><span className="block text-body font-medium">{action.label}</span><span className="block truncate text-caption text-muted">{action.detail}</span></span>
              </button>
            )) : <p className="px-3 py-8 text-center text-body text-muted">No matching action.</p>}
          </div>
          <p className="border-t border-border px-3 py-2 font-mono text-caption text-muted">↑↓ move · enter open · esc close</p>
        </DialogContent>
      </Dialog>
    </>
  );
}
