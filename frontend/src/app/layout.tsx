import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import Link from "next/link";
import { Toaster } from "sonner";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "Pixii Intelligence",
  description: "Content generation, improvement and analysis for Pixii.",
};

const NAV = [
  { href: "/", label: "Status" },
  { href: "/posts", label: "Corpus" },
  { href: "/templates", label: "Templates" },
  { href: "/studio", label: "Studio" },
  { href: "/scoreboard", label: "Scoreboard" },
];

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
    >
      <body className="min-h-full flex flex-col">
        <nav className="border-b border-black/10 dark:border-white/15">
          <div className="mx-auto flex max-w-5xl gap-5 px-6 py-3 text-sm">
            {NAV.map((item) => (
              <Link key={item.href} href={item.href} className="opacity-70 hover:opacity-100">
                {item.label}
              </Link>
            ))}
          </div>
        </nav>
        {children}
        {/* The one toast mount. sonner carries its own `"use client"`, so it drops into this
            server layout without a client boundary around the app.

            Colours come through sonner's documented CSS variables rather than a className,
            because its own selectors are two attributes deep and would win against a single
            utility class. `richColors` is what routes an error toast to the `--error-*` set —
            without it every toast renders `--normal-*` and a failure looks like a
            confirmation.

            `--error-text` is `--text`, NOT `--danger`. Measured: #D6455D is 4.31:1 on
            --surface light and 4.00:1 dark, so it fails AA as text (the PRD's claim that the
            status colours are "AA on both surfaces" does not hold for text). It clears the
            3:1 bar for a non-text indicator, so it carries the border instead — the same
            fill-not-text rule US-001 set for the brand orange. A failure is therefore marked
            by sonner's error icon, the danger border and the words themselves, never by
            colour alone.

            ponytail: sonner's entrance is its own hardcoded 400ms, not the PRD's 200ms
            structural token — it exposes no duration variable, and its selectors outrank a
            utility class. The `prefers-reduced-motion` block in globals.css still zeroes it,
            since that rule is `!important` on every element. Ceiling: a `[data-sonner-toast]`
            override in globals.css if 400ms ever reads as sluggish. */}
        <Toaster
          richColors
          position="bottom-right"
          style={
            {
              "--normal-bg": "var(--surface)",
              "--normal-text": "var(--text)",
              "--normal-border": "var(--border)",
              "--error-bg": "var(--surface)",
              "--error-text": "var(--text)",
              "--error-border": "var(--danger)",
            } as React.CSSProperties
          }
        />
      </body>
    </html>
  );
}
