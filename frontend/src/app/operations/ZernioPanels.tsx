"use client";

import { useState } from "react";

import { postJson, type IngestResult, type MetricsSyncResult } from "@/lib/api";

import ConfirmedAction, { type Prerequisite } from "./ConfirmedAction";
import { Counts } from "./Counts";

/** The one credential both Zernio pulls need, worded for each.
 *
 *  Built per panel rather than shared as a constant because `consequence` differs: an ingest
 *  without Zernio reaches no corpus at all, while a sync without it leaves numbers that are
 *  simply older than they look — and "your data is stale" and "nothing will happen" are two
 *  different things to tell someone. */
function zernio(configured: boolean | undefined, consequence: string): Prerequisite {
  return {
    credential: "zernio",
    label: "Zernio",
    // `Boolean(...)`, so a key missing from `credentials` reads as not configured rather than
    // as `undefined` — which is falsy and would render the badge with no word in it.
    met: Boolean(configured),
    consequence,
  };
}

/** `POST /corpus/ingest` — pull posts and their metrics from Zernio into the corpus.
 *
 *  `credentials` is `null` when `GET /health` itself failed; `ConfirmedAction` turns that into
 *  "unknown, controls left enabled" rather than into "not configured". */
export function CorpusIngestPanel({ credentials }: { credentials: Record<string, boolean> | null }) {
  // Opt-in only in the sense that it can be turned *off*: the route defaults `with_media` to
  // true and the media is the reason a visual template has anything to extract from. The
  // checkbox exists so an ingest can be run without touching the network for 60-odd images.
  const [withMedia, setWithMedia] = useState(true);

  return (
    <ConfirmedAction<IngestResult>
      title="Pull the corpus from Zernio"
      lede="Reads every post and the recent metrics window, and folds both into the corpus. This is where the raw material extraction learns from comes from."
      actionLabel="Pull from Zernio"
      consequence="This reaches Zernio and reads your account. It writes posts into the local corpus and publishes nothing, schedules nothing and changes nothing on the account."
      rerun="Safe to run again: posts upsert on Zernio's own id, so a second run updates the rows it already wrote rather than duplicating them, and metrics move as posts accumulate engagement."
      prerequisites={credentials === null ? null : [
        zernio(
          credentials.zernio,
          "`ZERNIO_API_KEY` is not set, so there is nothing to read the corpus from — this request would be refused before it reached an account.",
        ),
      ]}
      compose={() => ({
        facts: [
          { label: "Reads", value: "Zernio /analytics (recent 50-row window) and /v1/posts (full history)" },
          { label: "Writes", value: "posts and metric snapshots, in this database only" },
          { label: "Media", value: withMedia ? "downloaded with each post" : "not downloaded — text only" },
        ],
        // The flag is read here, once, and closed over — so the request that goes out is the
        // one the confirmation described. See `Command`.
        run: () => postJson<IngestResult>(`/corpus/ingest?with_media=${withMedia}`),
      })}
      renderResult={(data) => (
        <Counts
          rows={[
            { label: "Fetched", value: data.fetched, hint: "from the analytics window" },
            { label: "Created", value: data.created },
            { label: "Updated", value: data.updated },
            { label: "History read", value: data.history_fetched, hint: "from the full post list" },
            { label: "Recovered", value: data.history_recovered, hint: "posts the window missed" },
          ]}
          note="Zero created with a non-zero fetch is the normal result of a repeat run: every post read was already here and was updated in place."
        />
      )}
    >
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
            — needed before a post&apos;s image can be promoted into Assets or read by visual
            extraction. Unticking makes this a text-only read.
          </span>
        </span>
      </label>
    </ConfirmedAction>
  );
}

/** `POST /metrics/sync` — refresh every post's numbers now and append a snapshot for each. */
export function MetricsSyncPanel({ credentials }: { credentials: Record<string, boolean> | null }) {
  return (
    <ConfirmedAction<MetricsSyncResult>
      title="Sync engagement from Zernio"
      lede="Refreshes every post's metrics and appends one reading per post. This is also what notices that a pushed draft has been published by a human, which is the step that moves it into the verdict queue."
      actionLabel="Sync metrics"
      consequence="This reaches Zernio and reads your account's analytics. It writes metric snapshots locally and commands nothing."
      rerun="Safe to run again, and meant to be: readings accumulate rather than overwrite, so each run appends a row per post and builds the engagement curve. Running it twice in a minute writes two nearly identical readings, which is honest rather than harmful."
      prerequisites={credentials === null ? null : [
        zernio(
          credentials.zernio,
          "`ZERNIO_API_KEY` is not set, so engagement cannot be refreshed and every number in the corpus stays as old as its last successful sync.",
        ),
      ]}
      compose={() => ({
        facts: [
          { label: "Reads", value: "Zernio /analytics for this account" },
          { label: "Writes", value: "one metric snapshot per post, appended" },
          { label: "Also detects", value: "drafts that a human has since published" },
        ],
        run: () => postJson<MetricsSyncResult>("/metrics/sync"),
      })}
      renderResult={(data) => (
        <Counts
          rows={[
            { label: "Fetched", value: data.fetched },
            { label: "Snapshots", value: data.snapshots, hint: "readings appended" },
            { label: "Newly live", value: data.went_live, hint: "drafts found published" },
            { label: "Created", value: data.created },
            { label: "Updated", value: data.updated },
          ]}
          note="`Newly live` is the only number here that closes a gate: it moves drafts into the Inbox's published-awaiting-verdict queue. Zero means no pushed draft has been published since the last sync."
        />
      )}
    />
  );
}
