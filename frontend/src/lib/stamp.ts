/** A naive ISO timestamp from the API, shown as the UTC instant it is.
 *
 *  **Never `new Date(value)`.** Every datetime column in this database is `timestamp without
 *  time zone`, so these arrive with no offset — and an offsetless ISO string with a time in it
 *  is parsed by JavaScript as *local*, which would silently shift every timestamp on this
 *  panel by the reader's own offset and label the result UTC. Sliced as text instead, which
 *  cannot be wrong in a way nobody notices.
 *
 *  **In `lib/` and not in `studio/PublishPanel.tsx`, where it was written, because a server
 *  component cannot call a function exported from a `"use client"` module.** The import does
 *  not fail: it succeeds and hands back a client *reference*, and calling it throws
 *  `Attempted to call stamp() from the server but stamp is on the client` at render time. That
 *  is how `/operations` — a server component rendering three panels that print timestamps —
 *  served its loading skeleton and nothing else while every test was green. No vitest test can
 *  catch it, ever: jsdom renders server and client components the same way and enforces no
 *  boundary between them. It was found by building the app, curling the page and reading the
 *  server log, which is the standard `CLAUDE.md` sets for anything visual and the third defect
 *  in this project's history that only that found.
 *
 *  `PublishPanel` re-exports this so its own importers keep working. New callers on the server
 *  must import from here directly — a re-export through a `"use client"` module still crosses
 *  the boundary. */
export function stamp(value: string | null): string {
  if (!value) return "—";
  return `${value.slice(0, 16).replace("T", " ")} UTC`;
}
