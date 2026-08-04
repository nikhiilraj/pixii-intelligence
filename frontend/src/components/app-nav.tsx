"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { CommandPalette } from "@/components/command-palette";

const NAV = [
  { href: "/", label: "Inbox", match: (path: string) => path === "/" },
  { href: "/studio", label: "Studio", match: (path: string) => path.startsWith("/studio") },
  { href: "/posts", label: "Corpus", match: (path: string) => path.startsWith("/posts") },
  {
    href: "/templates",
    label: "Templates",
    match: (path: string) => path.startsWith("/templates") || path.startsWith("/scoreboard"),
  },
  { href: "/assets", label: "Assets", match: (path: string) => path.startsWith("/assets") },
];

/** The small client boundary that gives the otherwise-server-rendered shell a sense of place. */
export function AppNav() {
  const pathname = usePathname();

  return (
    <nav aria-label="Primary" className="border-b border-border bg-surface/80 backdrop-blur-sm">
      <div className="mx-auto flex max-w-6xl items-center gap-6 overflow-x-auto px-6">
        <Link
          href="/"
          aria-label="Pixii Intelligence home"
          className="my-2.5 flex shrink-0 items-center gap-2 pr-2 font-semibold tracking-tight"
        >
          <span
            aria-hidden="true"
            className="grid size-7 place-items-center rounded-input bg-text font-mono text-caption text-bg"
          >
            PI
          </span>
          <span className="hidden sm:inline">Pixii Intelligence</span>
        </Link>

        <div className="flex min-w-max self-stretch">
          {NAV.map((item) => {
            const active = item.match(pathname);
            return (
              <Link
                key={item.href}
                href={item.href}
                aria-current={active ? "page" : undefined}
                className={`relative flex min-h-12 items-center px-3 text-meta font-medium transition-colors ${
                  active ? "text-text" : "text-muted hover:text-text"
                }`}
              >
                {item.label}
                {active && (
                  <span
                    aria-hidden="true"
                    className="absolute inset-x-3 bottom-0 h-0.5 bg-accent"
                  />
                )}
              </Link>
            );
          })}
        </div>

        <CommandPalette />
      </div>
    </nav>
  );
}
