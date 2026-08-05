"use client";

import { useState, type ReactNode } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import type { ApiFailure, ApiResult } from "@/lib/api";

/** One thing that has to be configured before an operation can work, and what its absence
 *  costs.
 *
 *  `met` comes from `GET /health`'s `credentials` map, which reports presence and never a
 *  value. `consequence` is the sentence shown when it is missing — not "credential missing"
 *  but what will actually happen, because an operator standing in front of a disabled button
 *  needs to know whether to fix a key or to stop. */
export type Prerequisite = { credential: string; label: string; met: boolean; consequence: string };

/** A command a person asked for and has not yet confirmed — everything about the request,
 *  frozen at the moment the confirmation opened.
 *
 *  **Frozen, not re-read at fire time.** Direct analogue of `Pending.revision` in
 *  `studio/PublishPanel.tsx`: if the confirmation says "2 drafts, about 5 chat completions"
 *  and the request is composed again when Confirm is pressed, the reviewer confirmed a
 *  description of a run other than the one that goes out. The whole value of this screen is
 *  that the sentence on the dialog and the request on the wire are the same thing. */
export type Command<T> = {
  /** The `dt`/`dd` rows on the confirmation. Strings, already formatted — this component
   *  never computes a number, so it can never disagree with the panel that composed it. */
  facts: { label: string; value: string }[];
  run: () => Promise<ApiResult<T>>;
};

/** The generic failure card. Network and HTTP say different things, which is the entire
 *  reason `ApiResult` is three-way.
 *
 *  `failure.message` is the API's own `detail` where there was one, already unflattened by
 *  `messageFrom` — including the object shape the batch routes raise, so a 502 carrying
 *  `{error, llm_calls, image_calls}` reads as its reason followed by what it spent. */
function FailureCard({ failure }: { failure: ApiFailure }) {
  return (
    <Card role="alert" className="border-danger/40 bg-danger/10 text-meta">
      <p className="font-medium">
        {failure.kind === "network"
          ? "The API could not be reached."
          : `The request was refused — HTTP ${failure.status}`}
      </p>
      <p className="mt-1 wrap-anywhere text-muted">
        {failure.message}
        {failure.kind === "network" &&
          " Nothing was sent — this request never arrived, so nothing outside this machine was touched."}
      </p>
    </Card>
  );
}

/** An operation that costs money or reaches outside, behind a confirmation.
 *
 *  Three properties this exists to keep, all of them borrowed from `studio/PublishPanel.tsx`,
 *  which is this repo's confirmation precedent:
 *
 *  1. **The button that names the action does not fire it.** It ends in an ellipsis and opens
 *     a dialog naming the action, the destination, what the request will contain, and what
 *     happens if it is run twice. A one-click paid external call is what this prevents.
 *  2. **A missing prerequisite is said before the action is composed, not after it fails.**
 *     Discovering that Zernio is unconfigured by pressing a button and reading a 502 is a bad
 *     trade when `GET /health` answers the question up front. The server is still what
 *     decides — this only decides what a control looks like.
 *  3. **An unread prerequisite is not a failed one.** `prerequisites === null` means the
 *     `/health` read itself failed; the controls stay enabled and the server answers, because
 *     disabling on a request that never arrived hides a capability that is working. Exactly
 *     the `publishing === null` rule in PublishPanel.
 *
 *  ponytail: no toast, no polling, no undo, no history list. A result is rendered in place and
 *  stays until something else is run. Deliberately *not* a fake "commands issued" trail —
 *  `PublishPanel`'s history is `GET /drafts/{id}/publications`, a real audit table; there is no
 *  such table behind these four routes, and a list that only knows this browser tab's session
 *  would read like one and be wrong the moment the page reloads. What a re-run does is stated
 *  on the confirmation instead. Ceiling: a real run-history route the day one exists.
 */
export default function ConfirmedAction<T>({
  title,
  lede,
  actionLabel,
  consequence,
  rerun,
  prerequisites,
  blockedReason = null,
  compose,
  renderResult,
  renderFailure,
  children,
}: {
  title: string;
  lede: string;
  /** Names the action, in the words the confirmation repeats. Rendered with a trailing
   *  ellipsis on the button, without one on the Confirm. */
  actionLabel: string;
  /** What pressing Confirm will actually do, including where it reaches. */
  consequence: string;
  /** What happens if this is run a second time. Every one of these routes is re-runnable and
   *  says so differently — upsert, append, or generate again — and "is it safe to press
   *  twice" is the question an operator actually has. */
  rerun: string;
  prerequisites: Prerequisite[] | null;
  /** Why this cannot be composed *yet* — an empty field, unparseable input. A different thing
   *  from a missing prerequisite, which is about configuration and is not fixed by typing, so
   *  it is a separate prop rather than a fake credential row wearing a badge. `null` when
   *  there is nothing in the way. */
  blockedReason?: string | null;
  compose: () => Command<T>;
  renderResult: (data: T) => ReactNode;
  renderFailure?: (failure: ApiFailure) => ReactNode;
  /** The controls that shape the request — a cap, a checkbox, a textarea. Above the button. */
  children?: ReactNode;
}) {
  const [pending, setPending] = useState<Command<T> | null>(null);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<T | null>(null);
  const [failure, setFailure] = useState<ApiFailure | null>(null);

  const missing = (prerequisites ?? []).filter((p) => !p.met);
  // `false` only where a read said so. An unread `/health` leaves this false too, and the
  // difference is carried by `prerequisites === null` wherever it matters.
  const blocked = missing.length > 0;

  async function fire(command: Command<T>) {
    setBusy(true);
    const outcome = await command.run();
    setBusy(false);
    setPending(null);

    if (outcome.ok) {
      setResult(outcome.data);
      setFailure(null);
    } else {
      setFailure(outcome);
      setResult(null);
    }
  }

  return (
    <section className="space-y-3 rounded-card border border-border p-4">
      <div>
        <h3 className="text-head font-medium">{title}</h3>
        <p className="mt-1 text-meta text-muted">{lede}</p>
      </div>

      {/* Presence flags, said up front. One row per credential this operation needs, so an
          operator reads "Zernio is configured" rather than inferring it from a button that
          happens to be enabled. */}
      {prerequisites === null ? (
        <Card className="bg-surface-2 text-meta">
          <p className="font-medium">Configuration could not be read.</p>
          <p className="mt-1 text-muted">
            <code className="font-mono">GET /health</code> failed, so whether this operation&apos;s
            credentials are configured is unknown rather than absent. The control is left enabled
            because the server, not this page, is what decides.
          </p>
        </Card>
      ) : (
        prerequisites.length > 0 && (
          <ul className="flex flex-wrap items-center gap-2">
            {prerequisites.map((p) => (
              <li key={p.credential}>
                <Badge variant={p.met ? "success" : "danger"}>
                  {p.label} {p.met ? "configured" : "not configured"}
                </Badge>
              </li>
            ))}
          </ul>
        )
      )}

      {missing.map((p) => (
        <p key={p.credential} className="text-meta text-amber-700 dark:text-amber-400">
          {p.consequence}
        </p>
      ))}

      {children}

      {blockedReason && <p className="text-meta text-amber-700 dark:text-amber-400">{blockedReason}</p>}

      <div className="flex flex-wrap gap-2 border-t border-border pt-3">
        {/* The trailing ellipsis is the promise: a question follows, and one does. `compose`
            runs here and once — see `Command`. */}
        <Button
          variant="outline"
          disabled={busy || blocked || blockedReason !== null}
          onClick={() => {
            setFailure(null);
            setPending(compose());
          }}
        >
          {actionLabel}…
        </Button>
      </div>

      {failure && (renderFailure ? renderFailure(failure) : <FailureCard failure={failure} />)}
      {result !== null && renderResult(result)}

      {/* Mounted only while something is pending, so nothing on the dialog is left over from a
          previous confirmation — the reason PublishPanel mounts its the same way. */}
      <Dialog open={pending !== null} onOpenChange={(isOpen) => !isOpen && setPending(null)}>
        {pending && (
          <DialogContent aria-describedby="operation-consequence">
            <DialogTitle>{actionLabel}?</DialogTitle>
            <DialogDescription id="operation-consequence">{consequence}</DialogDescription>

            <dl className="mt-4 grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-2 text-meta">
              {pending.facts.map((fact) => (
                <div key={fact.label} className="contents">
                  <dt className="text-muted">{fact.label}</dt>
                  <dd className="min-w-0 wrap-anywhere">{fact.value}</dd>
                </div>
              ))}
            </dl>

            <p className="mt-3 text-caption text-muted">{rerun}</p>

            <div className="mt-5 flex flex-wrap gap-2 border-t border-border pt-4">
              <Button disabled={busy} onClick={() => fire(pending)}>
                {busy ? "Running…" : `Confirm ${actionLabel.toLowerCase()}`}
              </Button>
              <Button variant="outline" disabled={busy} onClick={() => setPending(null)}>
                Cancel
              </Button>
            </div>
          </DialogContent>
        )}
      </Dialog>
    </section>
  );
}
