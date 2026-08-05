"use client";

import { useState } from "react";

import { Card } from "@/components/ui/card";
import { calls, postJson, type ApiFailure, type AutonomousRunResult } from "@/lib/api";

import ConfirmedAction, { type Prerequisite } from "./ConfirmedAction";
import { Counts } from "./Counts";

/** What one run will spend, at most — stated, never enforced.
 *
 *  Read straight off `autonomous.run_autonomous` and `generation.generate_draft` rather than
 *  off the route's docstring: one `propose_topics` completion for the run, then two per draft
 *  (`suggest_templates`, then the write, because an unattended run names no templates), and
 *  one HTML render per draft. That is `1 + 2n` completions and `n` renders, which is the same
 *  arithmetic behind the `?cap=500 → 1001 completions` figure `POST /drafts/autonomous-run`
 *  quotes as the reason its cap is clamped.
 *
 *  **At most, and every word of that matters.** A topic that fails after its suggestion call
 *  has still bought that call and buys no write; a run that proposes fewer topics than the cap
 *  spends less. The result reports what was *observed* by the server's own meter, and the two
 *  numbers are allowed to differ — which is why they are labelled differently on screen.
 *
 *  Counts, never a price: nothing in this application knows what a call cost. `calls()` in
 *  `lib/api` is the wording the repo already uses for exactly this. */
export function estimate(drafts: number): { completions: string; renders: string } {
  return {
    completions: calls(1 + 2 * drafts, "chat completion"),
    renders: calls(drafts, "image render"),
  };
}

/** The spend the 502 carries, pulled back out of the parsed `detail`.
 *
 *  `messageFrom` already folds it into the sentence, so the reason and the numbers do reach
 *  the screen without this. It is read again as structured values because a failed run is the
 *  one case where the spend is *all* there is to show — the drafts rolled back with the
 *  request and the money did not — and a number buried mid-sentence is not something an
 *  operator can compare against the estimate they just confirmed.
 *
 *  Off `detail`, never off `message`: regexing a sentence for a number is the class of bug
 *  `ApiFailure.detail` was added to prevent. `null` for any other failure shape — a network
 *  error spent nothing, and a proxy's HTML 502 carries no `detail` at all. */
export function spentOnFailure(failure: ApiFailure): { llm: number; image: number } | null {
  if (failure.kind !== "http") return null;
  const detail = failure.detail;
  if (typeof detail !== "object" || detail === null) return null;
  const { llm_calls: llm, image_calls: image } = detail as Record<string, unknown>;
  if (typeof llm !== "number" || typeof image !== "number") return null;
  return { llm, image };
}

/** `POST /drafts/autonomous-run` — a capped batch of drafts, generated here, pushed nowhere.
 *
 *  `ceiling` is `settings.autonomous_max_drafts` off `GET /health`, or `null` when that read
 *  failed. It is read **to say what a run will spend and for nothing else** — the route clamps
 *  `cap` server-side and would clamp it identically if this field said 500. Same relationship
 *  `variants_max` already has with Studio, and the reason the number is on the wire at all.
 */
export default function AutonomousRunPanel({
  credentials,
  ceiling,
}: {
  credentials: Record<string, boolean> | null;
  ceiling: number | null;
}) {
  // Seeded at the ceiling, which is also the route's default. An unread ceiling starts at 1 —
  // the smallest run that does anything — because guessing high on a paid operation is the
  // wrong way to be wrong.
  const [cap, setCap] = useState(ceiling ?? 1);

  // What the server will actually run, said in the client's own words. The clamp is the
  // server's; this only refuses to *describe* a run larger than the one that would happen.
  const effective = ceiling === null ? cap : Math.min(cap, ceiling);
  const clamped = ceiling !== null && cap > ceiling;
  const cost = estimate(effective);

  const prerequisites: Prerequisite[] | null =
    credentials === null
      ? null
      : [
          {
            credential: "azure_chat",
            label: "Azure chat",
            met: Boolean(credentials.azure_chat),
            consequence:
              "`AZURE_OPENAI_CHAT_API_KEY` is not set. Nothing can be written without it — the run would fail on its first call, having proposed no topics.",
          },
          {
            credential: "cloudflare_rendering",
            label: "HTML rendering",
            met: Boolean(credentials.cloudflare_rendering),
            consequence:
              "`CLOUDFLARE_BROWSER_RENDERING_TOKEN` is not set. The run can still write drafts — one bad renderer costs a topic its picture, not its words — but every draft it creates will arrive with no visual and will need redrawing in Studio.",
          },
        ];

  return (
    <ConfirmedAction<AutonomousRunResult>
      title="Run unattended generation now"
      lede="Proposes its own topics and writes a capped batch of drafts against the approved templates. The drafts land in the Inbox for review like any other."
      actionLabel="Run generation"
      consequence="This makes paid calls to the chat model and the renderer. It produces drafts in this system and reaches Zernio not at all: an unattended run cannot push, schedule or publish, and there is no control here that would let it."
      rerun="Safe to run again, and it generates again — this is not an upsert. A second run proposes fresh topics and writes a second batch, at the same cost as the first. Drafts you do not want are deleted in Studio."
      prerequisites={prerequisites}
      /* `min={1}` on a number input is a hint, not a guard: clearing the field gives `""`,
         `Number("")` is `0`, and `Math.min(0, ceiling)` is `0`. Without this the button stays
         enabled and the confirmation promises "at most 0 drafts" beside "at most 1 chat
         completion" — a call `run_autonomous` cannot make, because it returns an empty result
         before `propose_topics` when `cap <= 0`. A confirmation that describes a request the
         code will not issue is worse than no confirmation. */
      blockedReason={
        effective < 1
          ? "Set a cap of at least 1 — a run of zero drafts proposes no topics and does nothing."
          : null
      }
      compose={() => ({
        facts: [
          { label: "Drafts", value: `at most ${effective}` },
          // Named as an upper bound, beside the observed figures the result reports. See
          // `estimate` for why the two are allowed to disagree.
          { label: "Estimated spend", value: `at most ${cost.completions} and ${cost.renders}` },
          { label: "Produces", value: "drafts in this system, for review" },
          { label: "Sends", value: "nothing — an unattended run cannot reach Zernio" },
        ],
        // `effective` is read here, once, and closed over — the request that goes out is the
        // one the confirmation costed. See `Command` in ConfirmedAction.
        run: () => postJson<AutonomousRunResult>(`/drafts/autonomous-run?cap=${effective}`),
      })}
      renderResult={(data) => (
        <Counts
          rows={[
            { label: "Topics", value: data.topics, hint: "proposed" },
            { label: "Created", value: data.created, hint: "drafts written" },
            { label: "Failed", value: data.failed, hint: "topics that produced nothing" },
            {
              label: "Without a visual",
              value: data.visuals_failed,
              hint: "created, but the picture did not render",
            },
            { label: "Chat completions", value: data.llm_calls, hint: "observed" },
            { label: "Image renders", value: data.image_calls, hint: "observed" },
          ]}
          note="The last two are what the server's meter counted, not what was estimated: a topic that failed still bought its completion. Drafts without a visual are real and openable — redraw them in Studio."
        />
      )}
      renderFailure={(failure) => {
        const spent = spentOnFailure(failure);
        return (
          <Card role="alert" className="border-danger/40 bg-danger/10 text-meta">
            <p className="font-medium">
              {failure.kind === "network"
                ? "The API could not be reached."
                : `The run failed — HTTP ${failure.status}`}
            </p>
            <p className="mt-1 wrap-anywhere text-muted">{failure.message}</p>
            {spent && (
              <>
                {/* The drafts rolled back with the request; the money did not. This is the
                    only case where a spend is the entire result, so it is shown as figures
                    rather than left inside the sentence above. */}
                <dl className="mt-2 grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1 text-caption">
                  <dt className="text-muted">Chat completions spent</dt>
                  <dd className="font-mono tabular-nums">{spent.llm}</dd>
                  <dt className="text-muted">Image renders spent</dt>
                  <dd className="font-mono tabular-nums">{spent.image}</dd>
                </dl>
                <p className="mt-2 text-caption text-muted">
                  Any drafts written before the failure were rolled back with the request. These
                  calls were still made and still billed.
                </p>
              </>
            )}
          </Card>
        );
      }}
    >
      <div className="space-y-2">
        <label className="flex flex-wrap items-center gap-2 text-meta">
          <span className="font-medium">Draft cap</span>
          <input
            type="number"
            min={1}
            value={cap}
            onChange={(event) => setCap(Number(event.target.value))}
            aria-label="draft cap"
            className="min-h-8 w-20 rounded-input border border-border bg-transparent px-2 py-1.5 text-meta"
          />
        </label>

        {ceiling === null ? (
          <p className="text-caption text-muted">
            The configured ceiling could not be read, so the number above is shown as typed. The
            server clamps it either way — <code className="font-mono">cap</code> is enforced by
            the route, never here.
          </p>
        ) : clamped ? (
          <p className="text-caption text-amber-700 dark:text-amber-400">
            The configured ceiling is {ceiling}, so this run will produce at most {effective}{" "}
            drafts however large a number is typed. The clamp is the server&apos;s
            (<code className="font-mono">AUTONOMOUS_MAX_DRAFTS</code>); this line only says what
            it will do.
          </p>
        ) : (
          <p className="text-caption text-muted">
            The configured ceiling is {ceiling}. At {effective}, this run will spend at most{" "}
            {cost.completions} and {cost.renders} — counts, not a price; nothing here knows what
            a call cost.
          </p>
        )}
      </div>
    </ConfirmedAction>
  );
}
