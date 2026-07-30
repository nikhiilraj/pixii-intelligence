"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { postJson, VERDICT_NOTE_MAX, type Post, type Verdict } from "@/lib/api";

/* The only place a human can put judgement into this system.
 *
 * Shaped after `ExcludeToggle` — one client component, one `postJson` mutation, a `busy` flag,
 * the API's own message in a toast on failure, `router.refresh()` on success. It uses the
 * tokenised primitives rather than that file's pre-token class strings; the rest of this page
 * is a separate reskin.
 *
 * ponytail: three toggle buttons, not the vendored Radix Select. Three options that all fit on
 * one line need no popover, and the frontend testing policy says not to re-assert Radix's
 * behaviour — so a control built from `Button` + `aria-pressed` is both less code and the only
 * one whose selected state a jsdom test can honestly read.
 *
 * Deliberately no verdict history and no "who ruled": one ruling per post is the real
 * cardinality, matching the route and the row. */

// `Verdict`'s three members, with the words a human reads. The value is what goes on the wire.
const CHOICES: { value: Verdict; label: string }[] = [
  { value: "worked", label: "Worked" },
  { value: "didnt", label: "Didn't" },
  { value: "mixed", label: "Mixed" },
];

function labelOf(verdict: Verdict): string {
  return CHOICES.find((choice) => choice.value === verdict)?.label ?? verdict;
}

export default function VerdictForm({
  postId,
  verdict,
  note,
}: {
  postId: number;
  verdict: Verdict | null;
  note: string;
}) {
  const router = useRouter();
  // `recorded` is what the database holds; `choice` is what is currently picked in the form.
  // Separate, so a click that has not been saved yet cannot read as a saved ruling.
  const [recorded, setRecorded] = useState(verdict);
  const [choice, setChoice] = useState(verdict);
  // Seeded from the stored note, and that is load-bearing: the route assigns
  // `post.verdict_note = payload.note` unconditionally (main.py:301), so a box that started
  // empty would erase the reason someone wrote the moment they changed worked -> mixed.
  const [text, setText] = useState(note);
  const [busy, setBusy] = useState(false);
  // Whether the retract has been armed. Retracting throws away a human's judgement and the
  // reason they wrote for it, so it takes two clicks — see the block that renders it.
  const [armed, setArmed] = useState(false);

  /* One mutation for both directions, because the route is one route: `next === null` is the
   * retract. Kept as a parameter rather than reading `choice`, since the whole defect being
   * fixed was `choice` never being allowed to be null. */
  async function send(next: Verdict | null) {
    const clearing = next === null;
    // Starting a save is an implicit "not retracting". Without this, a user who armed the
    // retract and then hit save would see "Saving…" and "Retracting…" at once, since `busy` is
    // shared — a form claiming an action it is not performing, which is the bug this file exists
    // to avoid.
    if (!clearing) setArmed(false);
    setBusy(true);

    // The field is `note`, not `verdict_note`. `VerdictIn` (main.py:268) names it `note` and
    // gives it a default of "", so the wrong key would NOT be refused — it would save a ruling
    // whose reason is empty. And an empty note is skipped by `verdict_lessons`
    // (generation.py:211), so that one-word typo would record judgement that teaches nothing,
    // with no error anywhere to find it by. Pinned by test for the same reason.
    //
    // On a retract the note goes as "" rather than as whatever is in the box: the route empties
    // the column either way, so sending the old note would be a body that does not say what it
    // does — and the note is a reason *for a ruling* that no longer exists.
    const result = await postJson<Post>(`/posts/${postId}/verdict`, {
      verdict: next,
      note: clearing ? "" : text,
    });
    setBusy(false);

    if (!result.ok) {
      // Two messages, because "could not save" on a retract would read as though a ruling was
      // being written. Left armed, so the retry is one click.
      toast.error(clearing ? "Could not retract your ruling" : "Could not save your ruling", {
        description: result.message,
      });
      return;
    }

    // The display follows the row the route returned, not the click that caused it. `useState`
    // never re-seeds from changed props, so leaving this to `router.refresh()` alone would
    // leave the form showing the old ruling until the server component came back.
    setRecorded(result.data.verdict);
    setChoice(result.data.verdict);
    setText(result.data.verdict_note);
    setArmed(false);
    toast.success(clearing ? "Ruling retracted" : "Ruling saved");
    router.refresh();
  }

  const atCap = text.length >= VERDICT_NOTE_MAX;

  return (
    <Card className="mt-8">
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <h2 className="text-head font-semibold">Your ruling</h2>
        {recorded && <Badge>Recorded: {labelOf(recorded)}</Badge>}
      </div>

      <p className="mt-2 text-meta text-muted">
        Your call on how this post did, and why. One person on one post — it is judgement, not a
        measurement, and it says nothing about any other post.
      </p>

      <div
        role="group"
        aria-label="Your ruling on this post"
        className="mt-3 flex flex-wrap gap-2"
      >
        {CHOICES.map((option) => (
          <Button
            key={option.value}
            variant={choice === option.value ? "primary" : "outline"}
            aria-pressed={choice === option.value}
            onClick={() => setChoice(option.value)}
            disabled={busy}
          >
            {option.label}
          </Button>
        ))}
      </div>

      <label htmlFor="verdict-note" className="mt-4 block text-meta font-medium">
        Why — optional
      </label>
      <p className="mt-1 text-caption text-muted">
        The note is the lesson. A ruling with no reason written down is carried nowhere, because
        those words are the only part of it the generator ever reads.
      </p>
      <textarea
        id="verdict-note"
        value={text}
        // The browser's own guard, so the cap is reached rather than discovered: the route
        // answers 422 above 500 characters, and 500 itself is accepted.
        maxLength={VERDICT_NOTE_MAX}
        onChange={(event) => setText(event.target.value)}
        disabled={busy}
        rows={3}
        className="mt-2 w-full rounded-input border border-border bg-surface p-2 text-body disabled:bg-surface-2 disabled:text-muted"
      />

      <div className="mt-2 flex flex-wrap items-center justify-between gap-x-3 gap-y-2">
        <span className="text-caption text-muted" aria-live="polite">
          {text.length} of {VERDICT_NOTE_MAX} characters{atCap ? " — at the cap" : ""}
        </span>
        <Button
          onClick={() => choice !== null && void send(choice)}
          disabled={busy || choice === null}
        >
          {busy ? "Saving…" : recorded ? "Change the ruling" : "Record the ruling"}
        </Button>
      </div>

      {/* Only on a post that has a ruling: there is nothing to retract otherwise, and offering
        * it would imply a ruling exists.
        *
        * ponytail: the friction is one extra click and a changed label, not a modal and not a
        * timer. Two clicks is the standard weight for "discards a human judgement", and it is
        * the only weight available here — `button.tsx` has no danger variant on purpose
        * (`--accent` cannot carry text at AA), so the warning has to live in the copy. A
        * disarm-on-timeout would add flake and buy nothing over an explicit way out. */}
      {recorded && (
        <div className="mt-4 border-t border-border pt-3">
          <p className="text-caption text-muted">
            Retract if you should not have ruled at all — it takes the ruling and its reason off
            the post and puts it back in the inbox as unruled. It is not a way to say the post
            landed badly; that is Didn&apos;t.
          </p>
          <div className="mt-2 flex flex-wrap gap-2">
            {armed ? (
              <>
                <Button variant="outline" onClick={() => void send(null)} disabled={busy}>
                  {busy ? "Retracting…" : "Yes, retract it"}
                </Button>
                <Button variant="outline" onClick={() => setArmed(false)} disabled={busy}>
                  Keep the ruling
                </Button>
              </>
            ) : (
              <Button variant="outline" onClick={() => setArmed(true)} disabled={busy}>
                Retract the ruling
              </Button>
            )}
          </div>
        </div>
      )}
    </Card>
  );
}
