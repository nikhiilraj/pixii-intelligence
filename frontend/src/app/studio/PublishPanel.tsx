"use client";

import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  getJson,
  postJson,
  reviewReady,
  type ApiFailure,
  type Draft,
  type Publication,
  type PublicationAction,
  type PublishingTarget,
} from "@/lib/api";

/** What each command is called on screen. `ACTIONS` in backend/app/models/publication.py. */
export const ACTION_LABEL: Record<PublicationAction, string> = {
  schedule: "Schedule",
  publish_now: "Publish now",
  cancel_schedule: "Cancel schedule",
};

/** The route each command posts to. Beside the labels so the pair cannot drift apart. */
const ACTION_PATH: Record<PublicationAction, string> = {
  schedule: "schedule",
  publish_now: "publish",
  cancel_schedule: "cancel-schedule",
};

/** What a named zone's offset from UTC is at one instant, in milliseconds, or `null` for a
 *  name this engine does not know.
 *
 *  Reads the offset out of `Intl` rather than out of a table: the browser already ships the
 *  IANA database, and any table shipped here would be a second copy that goes stale at the
 *  next DST rule change. Formatting the instant *in* the zone and re-reading the fields as
 *  though they were UTC is the standard way to get a number back out of a formatter that
 *  only ever hands out strings.
 *
 *  `hour % 24`: with `hour12: false` some engines render midnight as "24" rather than "00",
 *  which would put the offset out by a day for exactly one hour of each night. */
function offsetMs(instant: number, timeZone: string): number | null {
  let parts: Intl.DateTimeFormatPart[];
  try {
    parts = new Intl.DateTimeFormat("en-US", {
      timeZone,
      hour12: false,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    }).formatToParts(new Date(instant));
  } catch {
    // `RangeError: invalid time zone`. A typo in a field a person types is not an exception.
    return null;
  }
  const at = (type: string) => Number(parts.find((part) => part.type === type)?.value);
  return (
    Date.UTC(at("year"), at("month") - 1, at("day"), at("hour") % 24, at("minute"), at("second")) -
    instant
  );
}

/** What a local time and a zone name add up to — one instant, or a reason there isn't one.
 *
 *  A union rather than `Date | null`, because "no instant" has three causes and they are three
 *  different things to tell someone: the zone name is not one this engine knows, the wall clock
 *  does not happen, or it happens twice. Collapsing them would put "check the timezone" in front
 *  of a reviewer whose timezone is fine.
 *
 *  These are exactly the refusals `distribution.resolve` raises, deliberately. The server is
 *  still the authority and still answers 422; this is the same question asked early enough that
 *  the answer arrives in the field rather than after a command. */
export type Resolved =
  | { kind: "ok"; utc: Date }
  | { kind: "incomplete" }
  | { kind: "unknown_zone" }
  | { kind: "nonexistent" }
  | { kind: "ambiguous" };

const DAY_MS = 24 * 60 * 60 * 1000;

/** The instant a wall-clock time in a named zone refers to — the browser's `distribution.resolve`.
 *
 *  Computed here as well as on the server, and that duplication is the point of this screen:
 *  ADR 0002 requires the reviewer to see the **resolved UTC instant** before firing, and a
 *  number the server would only reveal by acting on it is not a confirmation.
 *
 *  **The two wall clocks a year that are not one instant are refused, not resolved**, matching
 *  `distribution.resolve` case for case. 02:30 on a spring-forward morning does not happen;
 *  01:30 on a fall-back morning happens twice. Both would still yield *an* instant from any
 *  implementation that insists on one — and nothing makes this side's choice agree with the
 *  server's, so the confirmation could display one instant while the server scheduled another
 *  an hour away. That is the surprise the confirmation exists to remove, arriving on the one
 *  morning a reader would least expect it.
 *
 *  The algorithm is the standard one and is not the fixed-point iteration this replaced. Take
 *  the zone's offset a day either side of the wall clock — far enough to be on opposite sides
 *  of any transition — and build a candidate instant from each. A candidate is *valid* when the
 *  zone's offset at that instant is the offset it was built from, which is the same as saying it
 *  lands back on the wall clock asked for. Neither valid means the time does not exist; both
 *  valid and different means it happens twice. A fixed point cannot express either answer: it
 *  converges on one of them and reports success.
 *
 *  ponytail: two `Intl` reads and an equality test, no library and no `Temporal` polyfill. The
 *  browser already ships the IANA database; a table here would be a second copy going stale at
 *  the next rule change. */
export function resolveUtc(local: string, timeZone: string): Resolved {
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(local);
  if (!match) return { kind: "incomplete" };
  const wall = Date.UTC(
    Number(match[1]),
    Number(match[2]) - 1,
    Number(match[3]),
    Number(match[4]),
    Number(match[5]),
  );

  const before = offsetMs(wall - DAY_MS, timeZone);
  const after = offsetMs(wall + DAY_MS, timeZone);
  if (before === null || after === null) return { kind: "unknown_zone" };

  const early = wall - before;
  const late = wall - after;
  const earlyValid = offsetMs(early, timeZone) === before;
  const lateValid = offsetMs(late, timeZone) === after;

  if (!earlyValid && !lateValid) return { kind: "nonexistent" };
  if (earlyValid && lateValid && early !== late) return { kind: "ambiguous" };
  return { kind: "ok", utc: new Date(earlyValid ? early : late) };
}

/** `2026-08-12 03:30 UTC`, from a `Date`.
 *
 *  Not `toLocaleString`: the whole reason this line is on the confirmation is that it is the
 *  *un-localised* instant, and rendering it through the reader's own zone would show a third
 *  time beside the two already there and label it UTC. */
export function utcLabel(when: Date): string {
  return `${when.toISOString().slice(0, 16).replace("T", " ")} UTC`;
}

/** A naive ISO timestamp from the API, shown as the UTC instant it is.
 *
 *  **Never `new Date(value)`.** Every datetime column in this database is `timestamp without
 *  time zone`, so these arrive with no offset — and an offsetless ISO string with a time in it
 *  is parsed by JavaScript as *local*, which would silently shift every timestamp on this
 *  panel by the reader's own offset and label the result UTC. Sliced as text instead, which
 *  cannot be wrong in a way nobody notices. */
export function stamp(value: string | null): string {
  if (!value) return "—";
  return `${value.slice(0, 16).replace("T", " ")} UTC`;
}

/** One refusal, in the words this screen owes it.
 *
 *  Every status the publication routes answer with says something different, and flattening
 *  them into "the command failed" is what makes an operator retry a kill switch or reload a
 *  draft that was never pushed. `currentRevision` is non-null for exactly one of them — a
 *  stale command — because that is the only case a reload fixes. */
export type CommandError = {
  title: string;
  body: string;
  currentRevision: number | null;
};

/** Which refusal this is. Driven off `status` and the *parsed* `detail`, never off the message
 *  text — see `ApiFailure.detail` for why the parsed value is carried at all.
 *
 *  The two 409s are the reason this function exists rather than a switch inline. Both arrive
 *  as status 409 and only one of them can be fixed by looking again:
 *
 *  - `{error, current_revision}` — the draft moved under the reviewer. Reload and re-confirm.
 *  - a plain string — the draft was never pushed. There is nothing in Zernio to command.
 *
 *  The API's own `detail` is surfaced rather than rewritten wherever it is a sentence written
 *  for a person, which is all of them: 403 names the setting, 422 names what is not a moment,
 *  and 502 is Zernio's own refusal text, which is the only place its reason exists at all. */
export function classify(failure: ApiFailure, action: PublicationAction): CommandError {
  const what = ACTION_LABEL[action].toLowerCase();

  if (failure.kind === "network") {
    return {
      title: "The API could not be reached.",
      body: `${failure.message}. Nothing was sent — this request never arrived, so no command was recorded.`,
      currentRevision: null,
    };
  }

  if (failure.status === 403) {
    return {
      title: "Publishing is turned off.",
      body: `${failure.message} This is a capability that is switched off, not a fault: nothing is broken and retrying will not help. Turning it on is a decision — see ADR 0002.`,
      currentRevision: null,
    };
  }

  if (failure.status === 409) {
    const detail = failure.detail;
    const current =
      typeof detail === "object" && detail !== null
        ? (detail as { current_revision?: unknown }).current_revision
        : undefined;
    if (typeof current === "number") {
      return {
        title: "This draft changed while you were reading it.",
        body: `${failure.message} Nothing was sent. Reload to see the words as they are now, then confirm again.`,
        currentRevision: current,
      };
    }
    return {
      title: "This draft is not in Zernio.",
      body: `${failure.message} Push it first — there is no post to ${what} yet.`,
      currentRevision: null,
    };
  }

  if (failure.status === 422) {
    return {
      title: "That does not describe a moment.",
      // The API's sentence and nothing appended to it. Every 422 it raises already ends in
      // what to do — "Choose a time before or after the change." — so a generic tail of our
      // own ("change the time or the timezone") arrives second, vaguer, and occasionally
      // pointing at the wrong field. `resolve` now refuses three distinct cases and the
      // longest runs to two lines; a duplicate instruction under it is what makes it read as
      // a wall.
      body: `${failure.message} Nothing was sent.`,
      currentRevision: null,
    };
  }

  if (failure.status === 502) {
    return {
      title: "Zernio refused the command.",
      body: `${failure.message}`,
      currentRevision: null,
    };
  }

  return {
    title: `The ${what} command failed.`,
    body: `HTTP ${failure.status}: ${failure.message}`,
    currentRevision: null,
  };
}

/** Whether a resolved instant has already gone by.
 *
 *  At module scope rather than inline in the handler because `Date.now()` is impure and the
 *  React compiler's purity rule cannot tell a function in the component body from one that
 *  runs during render. It is called once, when the confirmation opens, and the answer is
 *  frozen with the rest of the command — see `Pending.past`. */
function hasPassed(utc: Date | null): boolean {
  return utc !== null && utc.getTime() <= Date.now();
}

/** The zone the reviewer's own machine is in, as the starting value for the field.
 *
 *  A starting value and nothing more: the schedule is for an audience, not for the person
 *  typing, and someone travelling would otherwise schedule in whichever zone their laptop
 *  woke up in without the field ever saying so. That is why the zone is shown on the
 *  confirmation as its own line rather than folded into the time. */
function browserZone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}

/** Every IANA name this engine knows, for the field's `<datalist>`, or `[]` where the engine
 *  does not offer the list.
 *
 *  ponytail: a native `<input list=…>`, not a combobox and not a zone-picker dependency. The
 *  browser already ships the list and the field is typed into perhaps once a week. Ceiling: a
 *  real picker the day someone schedules across zones often enough to resent typing. */
function zoneNames(): string[] {
  const supported = (Intl as { supportedValuesOf?: (key: string) => string[] }).supportedValuesOf;
  try {
    return supported ? supported("timeZone") : [];
  } catch {
    return [];
  }
}

/** The command a reviewer has asked for and not yet confirmed. Frozen at the moment the
 *  confirmation opens — including `revision`.
 *
 *  **The revision is captured here, not read again when Confirm is pressed.** This screen's
 *  whole promise is that the command goes out against the version of the words that was on
 *  screen when it was confirmed; re-reading `draft.revision` at fire time would quietly hand
 *  the server whatever the draft had become, which is the guard defeating itself. If the draft
 *  did move, the server answers 409 with the number it is now on — which is the case this
 *  panel offers a reload for. */
type Pending = {
  action: PublicationAction;
  revision: number;
  /** Exactly the characters in the field, so the confirmation shows what was typed rather
   *  than a re-rendering of it. Empty for the two actions that name no time. */
  local: string;
  timezone: string;
  utc: Date | null;
  /** Whether that instant had already gone by when the confirmation opened.
   *
   *  Decided here rather than in the render for the reason the React compiler enforces:
   *  `Date.now()` during render is an impure call whose answer changes between renders, so the
   *  warning would appear and disappear on an unrelated re-render. Freezing it with the rest of
   *  the command is also the truer reading — this is a statement about the moment the reviewer
   *  was asked, not about the moment React happened to paint. */
  past: boolean;
};

/** One row of the audit trail. */
function PublicationRow({ publication }: { publication: Publication }) {
  const scheduled = publication.action === "schedule";
  return (
    <li className="min-w-0 border-t border-border py-2 first:border-t-0 first:pt-0">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-meta font-medium">{ACTION_LABEL[publication.action]}</span>
        <Badge
          variant={
            publication.state === "accepted"
              ? "success"
              : publication.state === "failed"
                ? "danger"
                : "warning"
          }
        >
          {publication.state}
        </Badge>
        <span className="text-caption text-muted">
          revision {publication.draft_revision} · {stamp(publication.created_at)}
        </span>
      </div>

      {/* `—` for the two actions that name no future time, and that is the honest rendering:
          the columns are NULL because no time was requested, not because a time of midnight
          was. `attempts` is the opposite case and prints its number even at 0 — a row is
          committed before the first attempt is counted, so `0` there is the measurement that
          says "written down, never sent". */}
      <dl className="mt-1 grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 text-caption text-muted">
        <dt>Requested for</dt>
        <dd className="min-w-0 wrap-anywhere">
          {scheduled && publication.requested_local_time
            ? `${publication.requested_local_time.replace("T", " ").slice(0, 16)} ${publication.timezone ?? "—"}`
            : "—"}
        </dd>
        <dt>Resolved UTC</dt>
        <dd className="min-w-0 wrap-anywhere">{scheduled ? stamp(publication.scheduled_utc) : "—"}</dd>
        <dt>Attempts</dt>
        <dd className="min-w-0">{publication.attempts}</dd>
        <dt>Accepted</dt>
        <dd className="min-w-0">{stamp(publication.accepted_at)}</dd>
      </dl>

      {publication.last_error && (
        // Zernio's own words. The only record of why anywhere in this system.
        <p className="mt-1 wrap-anywhere text-caption text-amber-700 dark:text-amber-400">
          {publication.last_error}
        </p>
      )}
    </li>
  );
}

/** Schedule, publish and cancel for a draft that is already in Zernio — and the confirmation
 *  step that ADR 0002 makes the condition of `PUBLISHING_ENABLED` being on at all.
 *
 *  Two things here are the slice rather than decoration:
 *
 *  1. **No command fires from the button that names it.** Every one of the three opens a
 *     confirmation showing the action, the destination account, the local time as typed, the
 *     IANA zone and the resolved UTC instant. A one-click publish is the thing the ADR exists
 *     to prevent, and the required `revision` alone protects against a *stale* command, never
 *     against a careless one.
 *  2. **The history is shown beside the buttons, never behind a tab.** A Publish button
 *     rendered without what has already been commanded is how one post gets scheduled twice —
 *     `GET /drafts/{id}/publications`' own docstring says so.
 *
 *  ponytail: no polling, no optimistic state, no undo. A command's result is prepended from
 *  the response, and a `requested` row that never reached a conclusion is left saying so until
 *  the page is reloaded — reconciliation is the metrics tick's job, on the server. Ceiling: a
 *  refresh control here the day a command is slow enough that the operator waits on it. */
export default function PublishPanel({
  draft,
  // `GET /publishing` — the destination and the kill switch. **`null` means the read failed**,
  // which is not `enabled: false` and not "there is no account". That distinction decides
  // whether anything gets disabled: a failed read leaves the controls alone and lets the
  // server answer, because disabling on a request that did not arrive would hide a capability
  // that is working. Only a read `false` turns the buttons off.
  publishing = null,
  // Every command already issued against this draft, newest first. `null` means the read
  // failed, which is not an empty history — and here that distinction is load-bearing in a way
  // it is not elsewhere: "nothing has been commanded" beside a Publish button, said on a
  // failed request, is the exact input that produces a second publication of one post.
  publications,
  // Hands a reloaded draft back to Studio. A stale-revision 409 is fixed by looking again, and
  // `page.tsx` keys this subtree on the `?draft=` string — which does not change on a refresh —
  // so a server round trip would re-render the page without reseeding the draft in state.
  onDraft,
}: {
  draft: Draft;
  publishing?: PublishingTarget | null;
  publications: Publication[] | null;
  onDraft: (draft: Draft) => void;
}) {
  const [local, setLocal] = useState("");
  const [timezone, setTimezone] = useState(browserZone);
  const [pending, setPending] = useState<Pending | null>(null);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<CommandError | null>(null);
  // Seeded from the prop and grown by this panel's own commands, for the reason `library` in
  // Studio is state rather than the prop: a row written here has to appear in the history
  // without a round trip through the server component. `null` still means the read failed.
  const [history, setHistory] = useState<Publication[] | null>(publications);

  // `false` only when the read said so. An unread target leaves this `false` too — and the
  // difference is carried by `publishing === null` at every place it matters, because "we do
  // not know" and "it is switched off" are two different things to tell someone standing in
  // front of a Publish button.
  const switchedOff = publishing !== null && !publishing.enabled;

  // The two states `distribution.submit` refuses schedule and publish for, said here before a
  // command is composed — the same trade the kill-switch card above makes. Discovering that a
  // draft failed review by pressing Publish and reading a 409 is a bad way to find out that
  // the post in Zernio does not say what this page says.
  //
  // **Computed, never trusted.** The server is still the only thing that enforces either, and
  // the 409 branches in `classify` stay: this is a read taken when the page rendered.
  const notReviewReady = !reviewReady(draft.generation_stage);
  // `pushed_revision` is null only for a draft that was never pushed, and this panel is not
  // rendered for one — Studio shows it against `zernio_post_id`. So a null here is a drift
  // rather than a "not applicable", and refusing on it is the safe direction: we do not know
  // what the post is carrying.
  const drifted = draft.pushed_revision !== draft.revision;
  // **Cancel is deliberately not in this.** It mirrors the exemption in `submit`'s docstring:
  // a draft scheduled while ready and rewritten since is exactly the state these two flags
  // describe, and withdrawing the schedule is the remedy for it. Disabling the one control
  // that reduces the exposure would leave the post to fire on its own schedule.
  const blocked = notReviewReady || drifted;
  const zones = zoneNames();
  const resolved: Resolved = resolveUtc(local, timezone);
  const utc = resolved.kind === "ok" ? resolved.utc : null;

  function open(action: PublicationAction) {
    const when = action === "schedule" ? utc : null;
    setFailure(null);
    setPending({
      action,
      // Read once, here. See `Pending`.
      revision: draft.revision,
      local: action === "schedule" ? local : "",
      timezone: action === "schedule" ? timezone : "",
      utc: when,
      past: hasPassed(when),
    });
  }

  async function fire(command: Pending) {
    setBusy(true);
    const result = await postJson<Publication>(
      `/drafts/${draft.id}/${ACTION_PATH[command.action]}`,
      command.action === "schedule"
        ? { revision: command.revision, local_time: command.local, timezone: command.timezone }
        : // `revision` and nothing else. It is required and has no default on the wire, which
          // is the whole stale-command guard — see `ScheduleIn` in api_drafts.py.
          { revision: command.revision },
    );
    setBusy(false);
    setPending(null);

    if (!result.ok) {
      setFailure(classify(result, command.action));
      return;
    }

    // Prepended, because the list is newest-first. A repeat of a command that already reached
    // a conclusion answers with the row it already has rather than a second one, so this is
    // de-duplicated by id — otherwise a double-press would draw two rows for one publication.
    setHistory((rows) => [result.data, ...(rows ?? []).filter((r) => r.id !== result.data.id)]);
    setFailure(null);
  }

  async function reload() {
    setBusy(true);
    const result = await getJson<Draft>(`/drafts/${draft.id}`);
    setBusy(false);
    if (result.ok) {
      onDraft(result.data);
      setFailure(null);
    }
  }

  const field = "min-h-8 w-full rounded-input border border-border bg-transparent px-2 py-1.5 text-meta";

  return (
    <section className="space-y-4 rounded-card border border-border p-4">
      <div>
        <p className="font-mono text-caption uppercase tracking-[0.12em] text-muted">Publication</p>
        <h3 className="mt-1 text-head font-medium">Schedule or publish</h3>
        <p className="mt-1 text-meta text-muted">
          This draft is in Zernio as post {draft.zernio_post_id}. Every command below is
          confirmed against revision {draft.revision} — the exact version of the words on this
          page — and shows you what it will do before it does it.
        </p>
      </div>

      {/* The kill switch, said before a command is composed rather than after one is refused.
          Discovering that a capability is off by attempting it and reading a 403 is a bad
          trade when the answer is one field on `GET /publishing`. The 403 branch in `classify`
          stays: this is a read taken when the page rendered, and the server is still the only
          thing that decides. */}
      {switchedOff && (
        <Card className="bg-surface-2 text-meta">
          <p className="font-medium">Publishing is switched off.</p>
          <p className="mt-1 text-muted">
            `PUBLISHING_ENABLED` is false, so schedule, publish and cancel commands are
            refused. Nothing else is affected — generating, reviewing and pushing a draft to
            Zernio all continue. Turning it on is a decision; see ADR 0002.
          </p>
        </Card>
      )}
      {/* Why the two controls that reach an audience are off, in the words of whichever
          state turned them off. One card and not two: a draft that was rewritten after being
          pushed is usually both — the rewrite set `failed_review` and moved the revision — and
          two stacked warnings saying the same thing twice is how a reader stops reading them.
          The stage is named first because it is what has to be fixed first. */}
      {blocked && (
        <Card className="bg-surface-2 text-meta">
          <p className="font-medium">
            {notReviewReady
              ? "This draft has not passed review."
              : "Zernio is holding an older version of this draft."}
          </p>
          <p className="mt-1 text-muted">
            {notReviewReady
              ? `Its workflow state is “${draft.generation_stage}”, so scheduling and publishing are refused. Complete the review flow — retry the workflow — before commanding a publication.`
              : `Post ${draft.zernio_post_id} carries revision ${draft.pushed_revision ?? "—"} and this draft is now at revision ${draft.revision}, so publishing it would put out words and a picture nobody confirmed. Push the draft again before scheduling or publishing it.`}{" "}
            Cancel schedule is still available: it withdraws an appointment without touching
            the words, which is the remedy for this state rather than another way to reach an
            audience.
          </p>
        </Card>
      )}
      {publishing === null && (
        <Card className="bg-surface-2 text-meta">
          <p className="font-medium">The publishing target could not be read.</p>
          <p className="mt-1 text-muted">
            This is a failed request, not a switched-off capability and not a missing account —
            the destination below is unknown rather than absent. The commands are left enabled
            because the server, not this page, is what decides.
          </p>
        </Card>
      )}

      <div className="grid gap-3 sm:grid-cols-2">
        <label className="min-w-0 space-y-1">
          <span className="text-caption font-medium uppercase tracking-label text-muted">
            Local time
          </span>
          {/* The native control, deliberately. ponytail: no date-picker dependency — a
              `datetime-local` gives a keyboard-typable field, a calendar, and the platform's
              own locale and 12/24-hour handling for free, and its value is always
              `YYYY-MM-DDTHH:mm`, which is exactly the naive string `ScheduleIn.local_time`
              wants. Ceiling: a real picker the day this needs a zone-aware calendar. */}
          <input
            type="datetime-local"
            value={local}
            onChange={(event) => setLocal(event.target.value)}
            aria-label="local time"
            className={field}
          />
        </label>

        <label className="min-w-0 space-y-1">
          <span className="text-caption font-medium uppercase tracking-label text-muted">
            Timezone
          </span>
          <input
            list="publish-zones"
            value={timezone}
            onChange={(event) => setTimezone(event.target.value)}
            aria-label="timezone"
            placeholder="Asia/Kolkata"
            className={field}
          />
          <datalist id="publish-zones">
            {zones.map((zone) => (
              <option key={zone} value={zone} />
            ))}
          </datalist>
        </label>
      </div>

      {/* The resolved instant, before anything is pressed as well as on the confirmation. The
          time and the zone are two fields and the instant is neither of them — showing it only
          in the dialog would mean the field that decides the hour never says what it decided. */}
      {/* The resolved instant — or the reason there is not one, in the words that fit the
          reason. Three refusals rather than one, mirroring `distribution.resolve`: telling a
          reviewer to check the timezone when the timezone is fine and the clocks simply moved
          is how a correct message becomes a wrong one. */}
      <p
        className={
          resolved.kind === "ok" || resolved.kind === "incomplete"
            ? "text-caption text-muted"
            : "text-caption text-amber-700 dark:text-amber-400"
        }
      >
        {resolved.kind === "incomplete"
          ? "Resolved UTC — once a local time is set."
          : resolved.kind === "unknown_zone"
            ? `“${timezone}” is not a timezone name this browser knows, so the instant cannot be resolved. An IANA name looks like Asia/Kolkata or Europe/London.`
            : resolved.kind === "nonexistent"
              ? `${local.replace("T", " ")} does not happen in ${timezone} — the clocks move forward over it. Choose a time before or after the change.`
              : resolved.kind === "ambiguous"
                ? `${local.replace("T", " ")} happens twice in ${timezone} — the clocks move back over it, so it names two different instants. Choose a time either side of the change.`
                : `${local.replace("T", " ")} in ${timezone} is ${utcLabel(resolved.utc)}.`}
      </p>

      <div className="flex flex-wrap gap-2 border-t border-border pt-4">
        <Button
          variant="outline"
          /* Nothing to confirm until there is one instant to confirm. The server refuses all
             four of these anyway; refusing here means the reviewer finds out in the field they
             are typing in rather than after committing to a command. */
          disabled={busy || switchedOff || blocked || resolved.kind !== "ok"}
          onClick={() => open("schedule")}
        >
          Schedule…
        </Button>
        {/* Not the primary variant, and that is not a style choice: the primary button on this
            page is Push, which creates a draft, and giving the one irreversible action on the
            screen the loudest treatment is how it gets pressed on the way past. */}
        <Button
          variant="outline"
          disabled={busy || switchedOff || blocked}
          onClick={() => open("publish_now")}
        >
          Publish now…
        </Button>
        {/* Not `blocked`. See the flag's own comment: this is the de-escalation. */}
        <Button variant="outline" disabled={busy || switchedOff} onClick={() => open("cancel_schedule")}>
          Cancel schedule…
        </Button>
      </div>

      {/* The trailing ellipsis on all three is the promise this component keeps: it says a
          question follows, and one does. */}

      {failure && (
        <Card role="alert" className="border-danger/40 bg-danger/10 text-meta">
          <p className="font-medium">{failure.title}</p>
          <p className="mt-1 wrap-anywhere text-muted">{failure.body}</p>
          {failure.currentRevision !== null && (
            <Button variant="outline" className="mt-2" disabled={busy} onClick={reload}>
              Reload this draft (now at revision {failure.currentRevision})
            </Button>
          )}
        </Card>
      )}

      <div className="space-y-2">
        <p className="font-mono text-caption uppercase tracking-[0.12em] text-muted">
          Commands issued
        </p>
        {history === null ? (
          <p className="text-meta text-amber-700 dark:text-amber-400">
            The command history could not be read. This is a failed request, not an empty
            history — this draft may already be scheduled or published, and this panel cannot
            see it. Reload before commanding anything.
          </p>
        ) : history.length === 0 ? (
          <p className="text-meta text-muted">
            Nothing has been commanded against this draft. It is a draft in Zernio and nowhere
            else.
          </p>
        ) : (
          <ul className="min-w-0">
            {history.map((publication) => (
              <PublicationRow key={publication.id} publication={publication} />
            ))}
          </ul>
        )}
      </div>

      {/* Mounted only while something is pending, so nothing on this dialog can be left over
          from a previous confirmation — the reason `AssetPicker` is mounted the same way. */}
      <Dialog open={pending !== null} onOpenChange={(isOpen) => !isOpen && setPending(null)}>
        {pending && (
          <DialogContent aria-describedby="confirm-consequence">
            <DialogTitle>{ACTION_LABEL[pending.action]}?</DialogTitle>
            <DialogDescription id="confirm-consequence">
              {pending.action === "cancel_schedule"
                ? "This withdraws the schedule and puts the post back to a draft in Zernio. The words and the picture stay exactly as they are."
                : "This takes the post out of draft state in Zernio. Nothing here can undo a post that has already gone out."}
            </DialogDescription>

            <dl className="mt-4 grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-2 text-meta">
              <dt className="text-muted">Action</dt>
              <dd className="min-w-0 font-medium">{ACTION_LABEL[pending.action]}</dd>

              <dt className="text-muted">Account</dt>
              <dd className="min-w-0 wrap-anywhere">
                {/* An account **identifier**, never presented as a person. Zernio holds the
                    human-readable label behind a call this app does not speak, and inventing
                    one — `voice_account` is right there and is a real name — would put a
                    plausible wrong answer on the one screen that exists to be checked.
                    `—` for both an unset id and a failed read; the line under the table says
                    which, because those are different states. `font-mono` because an opaque
                    hex id is compared character by character or not at all. */}
                {publishing?.account_id ? (
                  <span className="font-mono text-caption">
                    {publishing.platform} · {publishing.account_id}
                  </span>
                ) : (
                  "—"
                )}
              </dd>

              <dt className="text-muted">Local time</dt>
              <dd className="min-w-0 wrap-anywhere">
                {/* As typed. A schedule is "09:00 on the 12th" first and an instant second, and
                    the confirmation has to show the words back rather than a conversion of
                    them — if the zone's offset changes before the run, this is what said what
                    was meant. `—` for the two actions that name no time. */}
                {pending.local ? pending.local.replace("T", " ") : "—"}
              </dd>

              <dt className="text-muted">Timezone</dt>
              <dd className="min-w-0 wrap-anywhere">{pending.timezone || "—"}</dd>

              <dt className="text-muted">Resolved UTC</dt>
              <dd className="min-w-0 wrap-anywhere">{pending.utc ? utcLabel(pending.utc) : "—"}</dd>

              <dt className="text-muted">Draft revision</dt>
              <dd className="min-w-0">{pending.revision}</dd>
            </dl>

            {publishing === null ? (
              <p className="mt-3 text-caption text-muted">
                The destination could not be read, so the account is shown as an em dash rather
                than guessed. What can still be named is the post: this command updates Zernio
                post {draft.zernio_post_id}.
              </p>
            ) : publishing.account_id === null ? (
              <p className="mt-3 text-caption text-amber-700 dark:text-amber-400">
                No LinkedIn account is configured (`GETLATE_LINKEDIN_ID` is unset), so this
                command has nowhere to go and Zernio will refuse it.
              </p>
            ) : (
              <p className="mt-3 text-caption text-muted">
                That is Zernio&apos;s own account id, not a display name — this application does
                not hold the label. It is the id sent with every push of this draft, so it
                matches the account post {draft.zernio_post_id} already lives on.
              </p>
            )}

            {pending.past && (
              <p className="mt-3 text-caption text-amber-700 dark:text-amber-400">
                That instant has already passed. The API refuses a schedule in the past, so this
                will come back as a refusal rather than as a post.
              </p>
            )}

            <div className="mt-5 flex flex-wrap gap-2 border-t border-border pt-4">
              <Button disabled={busy} onClick={() => fire(pending)}>
                {busy ? "Sending…" : `Confirm ${ACTION_LABEL[pending.action].toLowerCase()}`}
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
