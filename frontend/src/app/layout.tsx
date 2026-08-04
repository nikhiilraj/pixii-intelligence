import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import { Toaster } from "sonner";

import { AppNav } from "@/components/app-nav";
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

/* Five destinations, in the order a post moves through them: what's waiting, where you build,
   what has been written, what you build from, what you build with.

   Two pages are deliberately NOT here, and both are still reachable:
   - **Status** was a destination; the Inbox is. The health rows it named now live in that page's
     footer ((inbox)/page.tsx), so nothing advertises Status as somewhere to go.
   - **Scoreboard** reads `/metrics/templates` — it is what the template library has done, not a
     sixth area of the app. It hangs off the Templates page lede, and its own empty state already
     links back, so the pair is bidirectional. Nav-level billing would also imply the scoreboard
     is somewhere you go to *decide* something; at 0 attributed posts it is somewhere you go to
     see that nothing has been decided yet.

   PRD.md:381's IA line lists six routes plus `/posts/[id]` — that is a route inventory, not a nav
   spec. Seven routes exist; five are destinations. Do not "restore" Scoreboard here. */
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
      {/* NOT a flex container, and that is load-bearing. While this was `flex flex-col`, every
          page's `<main className="mx-auto max-w-6xl">` was a flex item — and an auto margin on
          the cross axis disables `stretch`, so `<main>` fell back to shrink-to-fit and `mx-auto`
          centred whatever narrow box the content happened to produce. Measured at 1440: `/` sat
          at x=343 w=754 against a nav content edge of 144, `/posts` at 239/962, and the number
          moved with the database rather than staying wrong in a fixed way. Nothing consumed the
          flex — no `<main>` in the app carries `flex-1`, `grow` or `mt-auto`, and there is no
          sticky footer (the Inbox's `<footer>` is inside its own `<main>`). `items-stretch` is
          not the fix; auto margins beat it per spec. */}
      <body className="min-h-full">
        {/* One shell width, `max-w-6xl`, and it has to be every page or none. The active state
            lives in a deliberately small client component; the layout and page data stay on the
            server. Scoreboard remains represented by Templates because it is evidence about that
            library, not a sixth destination. */}
        <AppNav />
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
