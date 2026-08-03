"use client";

import Link from "next/link";
import { useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { calls, postJson, type Draft, type Spend } from "@/lib/api";

/* US-014, the post half, and Nikhil's own words for why it exists: "have the template and hooks
 * and visuals as kind of template so that we can easily recreate them using another topic."
 *
 * Shaped after `ExcludeToggle` — one client component, one `postJson` mutation, a `busy` flag,
 * the API's own message in a toast on failure — and styled like the block it sits in rather than
 * like `VerdictForm`, which is a token reskin this page has not had. Content only.
 *
 * It renders only inside the lineage branch of `page.tsx`, and that gate is the whole design:
 * `POST /drafts/retopic` answers 409 for a post this app did not generate, which is 57 of the
 * published posts here, so an ungated button would fail on almost every post it appeared on.
 *
 * No `router.refresh()` on success, unlike both siblings on this page: the new draft carries
 * `zernio_post_id: null`, so it is not the draft behind *this* post and nothing on this page
 * changes — a refresh would re-read `GET /posts/{id}/draft` and render exactly what is already
 * on screen. The link out is the thing the user needs, and it is rendered instead. */

/** `RetopicOut` (api_drafts.py:219) — `DraftOut` plus what producing it cost. The pair itself
 *  is `Spend` in `lib/api`, shared with Studio's batch: the ceiling named here when it was
 *  copied was "a second caller", and US-019 is it. */
type RetopicResult = Draft & Spend;

export default function RetopicForm({ postId }: { postId: number }) {
  const [idea, setIdea] = useState("");
  const [busy, setBusy] = useState(false);
  // The draft that came back, kept on screen: it holds the only copy of the spend the route
  // reported, and the id needed to go and read the draft.
  const [made, setMade] = useState<RetopicResult | null>(null);

  const subject = idea.trim();

  async function send() {
    setBusy(true);
    // Dropped before the call, not after it succeeds: the block below is a spend report, and
    // leaving the previous press's report up while this one is refused states that money was
    // spent on a draft that was never written. It describes the press that just happened or
    // nothing.
    setMade(null);

    // `source_post_id`, and this page's own row id — not `late_post_id`, which is what the
    // lineage join uses, and not `source_draft_id`, which would be accepted and would re-topic
    // whichever draft happens to carry this number. Exactly one of the two ids, or the route
    // answers 422.
    const result = await postJson<RetopicResult>("/drafts/retopic", {
      idea: subject,
      source_post_id: postId,
    });
    setBusy(false);

    if (!result.ok) {
      // 409 and 404 are different facts and the backend went out of its way to keep them apart:
      // 404 is an id no post carries, 409 is a post that exists but records no draft — or a
      // recorded template version that has since left the table. Telling a user their post does
      // not exist because its templates are unavailable is the conflation being avoided; the
      // API's own detail rides underneath, which is what keeps the two 409 sources apart.
      const headline =
        result.kind !== "http"
          ? "Could not write the new draft"
          : result.status === 409
            ? "The templates behind this post could not be re-topicked from"
            : result.status === 404
              ? "This post is no longer in the database"
              : "Could not write the new draft";
      toast.error(headline, { description: result.message });
      return;
    }

    setMade(result.data);
    toast.success("New draft written");
  }

  return (
    <div className="mt-8 rounded-lg border border-black/10 p-3 dark:border-white/15">
      <h2 className="text-xs uppercase tracking-wide text-muted">Re-topic</h2>
      <p className="mt-2 text-xs text-muted">
        A new draft on a new subject, written through these three template versions — the ones
        above, not whatever is newest in each family. This post is read and never changed.
        Writing one costs a chat completion and an image render, so it takes a subject and a
        press.
      </p>

      <label htmlFor="retopic-idea" className="mt-3 block text-xs text-muted">
        The new subject
      </label>
      {/* Stacked above the button rather than beside it: a full-width block cannot overflow its
          container, and this page is measured at 390px. */}
      <input
        id="retopic-idea"
        type="text"
        value={idea}
        onChange={(event) => setIdea(event.target.value)}
        disabled={busy}
        placeholder="What should this one be about?"
        className="mt-1 w-full rounded-md border border-black/20 bg-transparent px-3 py-2 text-sm dark:border-white/25"
      />

      {/* Disabled without a subject and while one is in flight. Both matter and for the same
          reason: a press is a chat completion and a render, so a stray click and a double click
          are each a draft nobody asked for. */}
      <Button onClick={() => void send()} disabled={busy || subject === ""} className="mt-3">
        Write the new draft
      </Button>

      {busy && (
        <p className="mt-2 text-xs text-muted" aria-live="polite">
          Writing — the model is being called and the visual rendered.
        </p>
      )}

      {made && (
        <div className="mt-3 border-t border-black/10 pt-3 dark:border-white/15">
          <p className="text-xs text-muted">
            Draft {made.id} written through the same three versions, and it spent{" "}
            {calls(made.llm_calls, "chat completion")} and {calls(made.image_calls, "image render")}.
            It is a draft: nothing has been pushed or published.
          </p>
          <Link
            href={`/studio?draft=${made.id}`}
            className="mt-2 inline-block text-sm text-muted underline"
          >
            Open draft {made.id} in the studio
          </Link>
        </div>
      )}
    </div>
  );
}
