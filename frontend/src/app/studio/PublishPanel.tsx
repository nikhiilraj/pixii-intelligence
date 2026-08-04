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
  type ApiFailure,
  type Draft,
  type Publication,
  type PublicationAction,
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

/** The instant a wall-clock time in a named zone refers to — the browser's `distribution.resolve`.
 *
 *  Computed here as well as on the server, and that duplication is the point of this screen:
 *  ADR 0002 requires the reviewer to see the **resolved UTC instant** before firing, and a
 *  number the server would only reveal by acting on it is not a confirmation. The server
 *  resolves again and stays the authority, so a past time or a zone name it rejects comes back
 *  as a 422 rather than as a post at the wrong hour.
 *
 *  **That does not cover every disagreement, and the gap is worth naming.** A wall clock that
 *  DST makes *nonexistent* (02:30 on a spring-forward morning) or *ambiguous* (01:30 on a
 *  fall-back morning) has no single answer: this returns one instant, `distribution.resolve`'s
 *  `astimezone` picks one too, and nothing guarantees they agree. The server would not refuse
 *  it — it would schedule an hour away from what this line displayed. Once a year per zone and
 *  one hour wide, so it ships: the publication row keeps `requested_local_time` and `timezone`
 *  verbatim, and those are what say what was meant when the instant is arguable.
 *
 *  Two passes, not one. The offset depends on the instant, and the instant is what is being
 *  solved for: guessing that the wall-clock fields are already UTC gives an offset within a
 *  day of the right one, and applying it lands on the answer. The second pass is what makes
 *  the hour either side of a DST transition come out right, where the first guess falls on the
 *  other side of the jump. ponytail: two passes, no library and no `temporal` polyfill — a
 *  fixed point this shallow converges in one step and the second is the proof.
 *
 *  `null` for a string `datetime-local` never produces, and for a zone this engine rejects. */
export function resolveUtc(local: string, timeZone: string): Date | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(local);
  if (!match) return null;
  const wall = Date.UTC(
    Number(match[1]),
    Number(match[2]) - 1,
    Number(match[3]),
    Number(match[4]),
    Number(match[5]),
  );

  let instant = wall;
  for (let pass = 0; pass < 2; pass += 1) {
    const offset = offsetMs(instant, timeZone);
    if (offset === null) return null;
    instant = wall - offset;
  }
  return new Date(instant);
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
      body: `${failure.message} Nothing was sent. Change the time or the timezone and confirm again.`,
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
  // The LinkedIn account this command would publish to. `null` means **the API does not report
  // it** — not that there is no account. Nothing on the wire carries it today:
  // `settings.getlate_linkedin_id` is what `publishing.py` actually sends and is not exposed,
  // and `settings.voice_account` is whose writing templates may describe, which is an
  // extraction setting and not a destination. Labelling the destination with the wrong one of
  // those is the conflation this codebase keeps finding, so the confirmation says `—` and
  // names the post id instead, which is the one destination fact the client can state
  // truthfully. A prop rather than a constant so this becomes one line in `page.tsx` the day
  // the field lands.
  account = null,
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
  account?: string | null;
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

  const zones = zoneNames();
  const preview = local ? resolveUtc(local, timezone) : null;
  const zoneKnown = resolveUtc("2000-01-01T00:00", timezone) !== null;

  function open(action: PublicationAction) {
    const utc = action === "schedule" ? preview : null;
    setFailure(null);
    setPending({
      action,
      // Read once, here. See `Pending`.
      revision: draft.revision,
      local: action === "schedule" ? local : "",
      timezone: action === "schedule" ? timezone : "",
      utc,
      past: hasPassed(utc),
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
      <p className="text-caption text-muted">
        {!zoneKnown
          ? `“${timezone}” is not a timezone name this browser knows, so the instant cannot be resolved. An IANA name looks like Asia/Kolkata or Europe/London.`
          : preview
            ? `${local.replace("T", " ")} in ${timezone} is ${utcLabel(preview)}.`
            : "Resolved UTC — once a local time is set."}
      </p>

      <div className="flex flex-wrap gap-2 border-t border-border pt-4">
        <Button
          variant="outline"
          disabled={busy || !local || !zoneKnown}
          onClick={() => open("schedule")}
        >
          Schedule…
        </Button>
        {/* Not the primary variant, and that is not a style choice: the primary button on this
            page is Push, which creates a draft, and giving the one irreversible action on the
            screen the loudest treatment is how it gets pressed on the way past. */}
        <Button variant="outline" disabled={busy} onClick={() => open("publish_now")}>
          Publish now…
        </Button>
        <Button variant="outline" disabled={busy} onClick={() => open("cancel_schedule")}>
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
                {/* `—`, never a guess. See the `account` prop. */}
                {account ?? "—"}
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

            {account === null && (
              <p className="mt-3 text-caption text-muted">
                The API does not report which LinkedIn account this publishes to, so the account
                is shown as <span aria-hidden>—</span>
                <span className="sr-only">an em dash</span> rather than guessed. What can be
                named is the post: this command updates Zernio post {draft.zernio_post_id}.
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
