"use client";

import { useState } from "react";

import { postJson, type LinkedInIngestResult } from "@/lib/api";

import ConfirmedAction from "./ConfirmedAction";
import { Counts } from "./Counts";

/** What the pasted text amounts to — a payload the route will accept, or a reason it will not.
 *
 *  Parsed here rather than posted hopefully, because the failure this prevents is not a 422:
 *  it is a *silent* one. `LinkedInScrapeIn` gives every field but `urn` a default, so a paste
 *  of the wrong shape — an array of strings, an object whose posts live under another key —
 *  can validate into a list of empty posts and be written into the corpus as evidence. The
 *  corpus is what extraction learns from; junk in it is not visible again until a template
 *  proposal comes back meaningless.
 *
 *  Two shapes are accepted, because both are what a scrape actually hands you: the route's own
 *  `{account, posts: [...]}` envelope, and a bare array of posts. Nothing else is guessed at.
 *
 *  Exported for its own test — this is the guard, and it is the part of this panel worth
 *  breaking on purpose. */
export type Parsed =
  | { kind: "empty" }
  | { kind: "invalid"; reason: string }
  | { kind: "ok"; account: string | null; posts: Record<string, unknown>[] };

export function parsePayload(text: string): Parsed {
  const trimmed = text.trim();
  if (!trimmed) return { kind: "empty" };

  let value: unknown;
  try {
    value = JSON.parse(trimmed);
  } catch (e) {
    return { kind: "invalid", reason: e instanceof Error ? e.message : "that is not valid JSON" };
  }

  const envelope = Array.isArray(value) ? { posts: value } : value;
  if (typeof envelope !== "object" || envelope === null) {
    return { kind: "invalid", reason: "the top level is neither an object nor an array of posts" };
  }

  const { posts, account } = envelope as { posts?: unknown; account?: unknown };
  if (!Array.isArray(posts)) {
    return {
      kind: "invalid",
      reason: "no `posts` array — paste either the scrape's `{account, posts: [...]}` object or a bare array of posts",
    };
  }
  if (posts.length === 0) {
    return { kind: "invalid", reason: "`posts` is empty, so this ingest would write nothing" };
  }

  for (const [index, post] of posts.entries()) {
    if (typeof post !== "object" || post === null || Array.isArray(post)) {
      return { kind: "invalid", reason: `post ${index + 1} is not an object` };
    }
    // `urn` is the only field with no default on the backend, so it is the only one whose
    // absence the route itself would catch. Checked here anyway, and named by position, so a
    // 200-post paste says which row to look at rather than which field.
    if (typeof (post as { urn?: unknown }).urn !== "string" || !(post as { urn: string }).urn) {
      return { kind: "invalid", reason: `post ${index + 1} has no \`urn\`` };
    }
  }

  return {
    kind: "ok",
    account: typeof account === "string" && account ? account : null,
    posts: posts as Record<string, unknown>[],
  };
}

/** `POST /corpus/linkedin` — fold a LinkedIn profile scrape into the corpus.
 *
 *  Reaches what Zernio cannot: posts predating its history, and repost counts, which Zernio
 *  reports as zero on every LinkedIn row. Nothing in this application performs the scrape —
 *  this takes its output — so the control is a paste field and honestly so.
 *
 *  ponytail: a textarea and `JSON.parse`, not a file upload and not a scraper. The route takes
 *  JSON, the scrape produces JSON, and a paste is how it gets from one to the other perhaps
 *  once a month. Ceiling: a file picker the day the payload is too large to paste. */
export default function LinkedInImportPanel() {
  const [text, setText] = useState("");
  const [withMedia, setWithMedia] = useState(false);

  const parsed = parsePayload(text);
  const blockedReason =
    parsed.kind === "empty"
      ? "Paste a scrape above to enable this."
      : parsed.kind === "invalid"
        ? `This will not be sent: ${parsed.reason}.`
        : null;
  const count = parsed.kind === "ok" ? parsed.posts.length : 0;

  return (
    <ConfirmedAction<LinkedInIngestResult>
      title="Import a LinkedIn scrape"
      lede="Folds a profile scrape into the corpus. This reaches posts Zernio's history does not carry, and repost counts, which Zernio reports as zero on every LinkedIn row."
      actionLabel="Import scrape"
      consequence="This writes posts into the local corpus, which is what template extraction learns from. With media downloading on it also fetches each post's images from LinkedIn; with it off nothing leaves this machine."
      rerun="Safe to run again: posts are matched on normalised content rather than on the URN, so re-importing a scrape that overlaps an earlier one updates those rows instead of duplicating them."
      // No credential: the payload arrives in the request. Media downloading reaches the
      // network but needs no key of ours, so there is nothing `/health` could report — an
      // empty list renders no badges rather than a reassuring row that means nothing.
      prerequisites={[]}
      blockedReason={blockedReason}
      compose={() => {
        // Re-parsed inside `compose` so the payload posted is built from the same read that
        // produced the count on the confirmation — see `Command`. A `kind !== "ok"` here is
        // unreachable while the button is disabled on exactly that condition; the empty
        // payload is the safe answer rather than a throw, because a throw in a click handler
        // would leave the panel with no result and no failure.
        const settled = parsePayload(text);
        const body =
          settled.kind === "ok"
            ? { account: settled.account, posts: settled.posts }
            : { account: null, posts: [] };
        return {
          facts: [
            { label: "Posts", value: String(settled.kind === "ok" ? settled.posts.length : 0) },
            {
              label: "Account",
              value: settled.kind === "ok" && settled.account ? settled.account : "— (none named in the paste)",
            },
            { label: "Writes", value: "posts in this database only" },
            {
              label: "Media",
              value: withMedia
                ? "each post's images downloaded from LinkedIn"
                : "not downloaded — nothing leaves this machine",
            },
          ],
          run: () =>
            postJson<LinkedInIngestResult>(`/corpus/linkedin?with_media=${withMedia}`, body),
        };
      }}
      renderResult={(data) => (
        <Counts
          rows={[
            { label: "Created", value: data.created },
            { label: "Updated", value: data.updated },
          ]}
          note="Matching is on normalised content, not the URN, so an update means the post was already in the corpus under some other id."
        />
      )}
    >
      <div className="space-y-2">
        <label className="block space-y-1">
          <span className="text-caption font-medium uppercase tracking-label text-muted">
            Scrape JSON
          </span>
          <textarea
            value={text}
            onChange={(event) => setText(event.target.value)}
            rows={6}
            aria-label="scrape JSON"
            placeholder={'{"account": "monte", "posts": [{"urn": "urn:li:activity:123", "content": "…"}]}'}
            className="min-h-24 w-full rounded-input border border-border bg-transparent px-2 py-1.5 font-mono text-caption"
          />
        </label>

        {/* Parsed as you type. The count is the useful confirmation — "47 posts" against a
            paste you believed held 50 is the discrepancy worth catching before it is written,
            and it is not visible from a request that succeeds. */}
        {parsed.kind === "ok" && (
          <p className="text-caption text-muted">
            {count} post{count === 1 ? "" : "s"} parsed
            {parsed.account ? ` for account ${parsed.account}` : ", with no account named"}.
          </p>
        )}

        <label className="flex items-center gap-2 text-meta">
          <input
            type="checkbox"
            checked={withMedia}
            onChange={(event) => setWithMedia(event.target.checked)}
            className="size-4 accent-accent"
          />
          <span>
            Download each post&apos;s media
            <span className="ml-1 text-muted">
              — off by default, because an import of text alone never touches the network.
            </span>
          </span>
        </label>
      </div>
    </ConfirmedAction>
  );
}
