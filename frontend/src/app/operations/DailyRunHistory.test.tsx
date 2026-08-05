import "@testing-library/jest-dom/vitest";

import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { ApiResult, DailyRunSummary } from "@/lib/api";

import { DailyRunHistory } from "./DailyRunHistory";

/* The two distinctions this panel exists to keep, and both of them are distinctions between
 * two things that render almost identically if you get them wrong:
 *
 *   `—` versus `0`     — nobody counted, versus counted and there were none
 *   empty versus failed — nothing has run, versus we could not ask
 *
 * Every assertion below is scoped to one run's `<li>`. `—` appears several times in a single
 * row (an unfinished run has three uncounted columns and no `finished_at`), so a bare
 * `getByText("—")` passes whatever the component does with any particular column — the same
 * shape as the six assertions in this project's history that checked for a 404 against routes
 * that did not exist.
 */

function row(runDate: string): HTMLElement {
  return document.querySelector(`[data-run="${runDate}"]`) as HTMLElement;
}

/** A finished run that produced nothing. Every count is a **measurement of zero**. */
const MEASURED_ZERO: DailyRunSummary = {
  id: 1,
  run_date: "2026-07-30",
  slot: "daily",
  status: "complete",
  started_at: "2026-07-30T03:30:00",
  finished_at: "2026-07-30T03:31:12",
  drafts_created: 0,
  topics_failed: 0,
  visuals_failed: 0,
  error: null,
  detail: null,
  notified_at: "2026-07-30T03:31:20",
};

/** A run that died before it counted anything. Every count is an **absence**. */
const NEVER_COUNTED: DailyRunSummary = {
  id: 2,
  run_date: "2026-07-31",
  slot: "daily",
  status: "failed",
  started_at: "2026-07-31T03:30:00",
  finished_at: "2026-07-31T03:30:04",
  drafts_created: null,
  topics_failed: null,
  visuals_failed: null,
  error: "no approved templates to generate from",
  detail: null,
  notified_at: null,
};

const ok = (data: DailyRunSummary[]): ApiResult<DailyRunSummary[]> => ({ ok: true, data });

afterEach(cleanup);

describe("a count that was never taken and a count that came back zero", () => {
  it("prints — for the run that never counted and 0 for the run that counted none", () => {
    // Both rows in one render, deliberately. With only the null row on screen, `{n || "—"}`
    // renders the same thing the correct code does and the test passes against the bug.
    render(<DailyRunHistory runs={ok([NEVER_COUNTED, MEASURED_ZERO])} />);

    const zero = within(row("2026-07-30"));
    expect(zero.getByText("Drafts created").nextElementSibling).toHaveTextContent(/^0$/);
    expect(zero.queryByText("—")).not.toBeInTheDocument();

    const absent = within(row("2026-07-31"));
    expect(absent.getByText("Drafts created").nextElementSibling).toHaveTextContent(/^—$/);
    expect(absent.queryByText("0")).not.toBeInTheDocument();
  });

  it("distinguishes the other two counts the same way", () => {
    render(<DailyRunHistory runs={ok([NEVER_COUNTED, MEASURED_ZERO])} />);

    for (const label of ["Topics failed", "Drafts without a visual"]) {
      expect(within(row("2026-07-30")).getByText(label).nextElementSibling).toHaveTextContent(
        /^0$/,
      );
      expect(within(row("2026-07-31")).getByText(label).nextElementSibling).toHaveTextContent(
        /^—$/,
      );
    }
  });
});

describe("nothing has run, versus we could not ask", () => {
  it("reads an empty list as an honest absence and not as a failure", () => {
    render(<DailyRunHistory runs={ok([])} />);

    expect(screen.getByText("No daily run has been recorded.")).toBeInTheDocument();
    // An empty list is the answer, not an error. Anything announcing itself here sends a
    // person looking for a broken scheduler on the day the scheduler is simply off.
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("reports a read that failed as a failed read, in the API's own words", () => {
    render(
      <DailyRunHistory
        runs={{ ok: false, kind: "http", status: 500, message: "daily_run does not exist", detail: null }}
      />,
    );

    expect(screen.getByRole("alert")).toHaveTextContent("daily_run does not exist");
    // And never the empty copy: "no daily run has been recorded" is a claim about the
    // database, and a request that did not arrive is not entitled to make it.
    expect(screen.queryByText("No daily run has been recorded.")).not.toBeInTheDocument();
  });

  it("says a backend that never answered is unreachable, not empty", () => {
    render(<DailyRunHistory runs={{ ok: false, kind: "network", message: "fetch failed" }} />);

    expect(screen.queryByText("No daily run has been recorded.")).not.toBeInTheDocument();
    expect(screen.getByText(/make api/)).toBeInTheDocument();
  });
});

describe("what a row says happened", () => {
  it("marks a failed run as failed and shows the reason it recorded", () => {
    render(<DailyRunHistory runs={ok([NEVER_COUNTED])} />);

    const failed = within(row("2026-07-31"));
    expect(failed.getByText("failed")).toBeInTheDocument();
    expect(failed.getByText(/no approved templates to generate from/)).toBeInTheDocument();
    // A quiet success and a failure must not read alike; the successful row carries neither.
    cleanup();
    render(<DailyRunHistory runs={ok([MEASURED_ZERO])} />);
    expect(within(row("2026-07-30")).getByText("complete")).toBeInTheDocument();
    expect(screen.queryByText(/no approved templates/)).not.toBeInTheDocument();
  });

  it("refuses to call a claimed-but-unfinished run healthy", () => {
    render(
      <DailyRunHistory
        runs={ok([{ ...NEVER_COUNTED, status: "running", finished_at: null, error: null }])}
      />,
    );

    const running = within(row("2026-07-31"));
    // "In progress" would assert that a process is alive. The row cannot know: a run working
    // now and a process that died holding the day are the same row until a later tick buries
    // it. The wording says what is recorded and stops there.
    expect(running.getByText("claimed, unfinished")).toBeInTheDocument();
    expect(running.queryByText(/in progress/i)).not.toBeInTheDocument();
    expect(running.getByText(/finished —/)).toBeInTheDocument();
  });

  it("reports an undelivered card without promising a retry it cannot make", () => {
    render(<DailyRunHistory runs={ok([{ ...MEASURED_ZERO, notified_at: null }])} />);

    const pending = within(row("2026-07-30"));
    expect(pending.getByText(/No notification recorded/)).toBeInTheDocument();
    // `run_daily_slot`'s retry path returns early on `not settings.teams_webhook_url`, so with
    // no webhook configured this column stays NULL forever and nothing is pending. The screen
    // cannot see which configuration it is in, so it names the condition instead of asserting
    // the happy one — "the next tick retries it" would be a promise about someone else's `.env`.
    expect(pending.getByText(/only while a Teams webhook is configured/)).toBeInTheDocument();
    // `notify_run` writes that column only once Teams accepts, so an undelivered card is the
    // retry working. Badging the run as failed for it would invent a failure.
    expect(pending.getByText("complete")).toBeInTheDocument();
    expect(pending.queryByText("failed")).not.toBeInTheDocument();
  });

  it("keeps the per-draft detail on its own lines", () => {
    render(
      <DailyRunHistory
        runs={ok([
          {
            ...MEASURED_ZERO,
            detail: "topic 'pricing' failed: no sources\ndraft 12 has no visual",
          },
        ])}
      />,
    );

    // Newline-joined by `run_daily_slot`. Rendered without `whitespace-pre-line` the two
    // messages run together, and a fixable one gets lost inside the sentence beside it.
    const detail = within(row("2026-07-30")).getByText(/pricing/);
    expect(detail).toHaveClass("whitespace-pre-line");
    expect(detail).toHaveTextContent("draft 12 has no visual");
  });

  it("renders the runs in the order the route returned them", () => {
    render(<DailyRunHistory runs={ok([NEVER_COUNTED, MEASURED_ZERO])} />);

    // Newest first is the route's chronology, and this panel does not re-sort it. Ordering by
    // drafts created would be a ranking of runs, which this project refuses everywhere.
    const dates = [...document.querySelectorAll("[data-run]")].map((li) =>
      li.getAttribute("data-run"),
    );
    expect(dates).toEqual(["2026-07-31", "2026-07-30"]);
  });

  it("says on screen which unattended work it cannot report on", () => {
    render(<DailyRunHistory runs={ok([])} />);

    // Only `daily.run_daily_slot` writes `DailyRun` rows. A complete-looking list beside a
    // silent metrics sync would imply coverage this screen does not have, so it names the
    // blind spot rather than leaving the reader to infer one.
    expect(screen.getByText(/metrics sync and the reconciliation pass/)).toBeInTheDocument();
  });
});
