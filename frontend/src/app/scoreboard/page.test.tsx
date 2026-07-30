import "@testing-library/jest-dom/vitest";

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import ScoreboardPage from "./page";

/* jsdom cannot measure layout, so this file does NOT claim the three tables align. That claim
 * was made in a browser and belongs there: at 1440px every column of every table now starts at
 * the same x — Template 168, Status 638, Posts 779, Engaged 873, Mean 1014, Evidence 1108 —
 * against 645/666/505 for Status before. The browser measurement is the verification.
 *
 * What jsdom can guard is the thing that would silently undo it: the three tables declaring
 * *different* column grids. That is a DOM fact, and it is asserted against the other tables
 * rather than against a hardcoded list, so retuning the grid is one edit and diverging it is a
 * failure.
 *
 * The other two assertions are the load-bearing copy the slice was told not to lose while
 * restructuring: every aggregate carries its sample count, and nothing is ranked. */

type Row = Record<string, unknown>;

function row(over: Partial<Record<string, unknown>> = {}): Row {
  return {
    family: "f",
    version: 1,
    kind: "hook",
    name: "a-template",
    status: "approved",
    sample_count: 0,
    total_engaged_actions: 0,
    total_impressions: 0,
    mean_engaged_actions: 0,
    sufficient: false,
    min_sample_size: 5,
    ...over,
  };
}

function stubFetch(rows: Row[]) {
  vi.stubGlobal(
    "fetch",
    vi.fn(() =>
      Promise.resolve(
        new Response(JSON.stringify(rows), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    ),
  );
}

/** The widths the browser actually lays out from — read off each table's own colgroup. */
function grids(): string[][] {
  return [...document.querySelectorAll("table")].map((t) =>
    [...t.querySelectorAll("colgroup col")].map((c) => c.className),
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
  cleanup();
});

describe("the scoreboard's three tables", () => {
  it("declares one column grid, identical across hook, structure and visual", async () => {
    stubFetch([
      // Deliberately lopsided: the longest name in one kind and the shortest in another is
      // exactly what auto layout used to size each table to.
      row({ kind: "hook", family: "h", name: "small-input-big-recurring-result" }),
      row({ kind: "structure", family: "s", name: "simple-diagnostic-engagement-lesson" }),
      row({ kind: "visual", family: "v", name: "grid" }),
    ]);

    render(await ScoreboardPage());

    const [hook, structure, visual] = grids();
    expect(hook).toHaveLength(6);
    expect(structure).toEqual(hook);
    expect(visual).toEqual(hook);
  });

  it("keeps every aggregate's sample count and the under-threshold flag", async () => {
    stubFetch([
      row({ kind: "hook", family: "thin", sample_count: 3, sufficient: false }),
      row({ kind: "hook", family: "ok", version: 2, sample_count: 9, sufficient: true }),
    ]);

    render(await ScoreboardPage());

    // The flag names the count and the threshold; shortening it to fit a column would drop
    // the one number that says the figure cannot be read yet.
    expect(screen.getByText("too thin (3/5)")).toBeInTheDocument();
    expect(screen.getByText("enough to read")).toBeInTheDocument();
    // And the raw counts are still on the row.
    expect(screen.getByText("3")).toBeInTheDocument();
    expect(screen.getByText("9")).toBeInTheDocument();
  });

  it("still says the templates are not ranked", async () => {
    stubFetch([row()]);

    render(await ScoreboardPage());

    expect(screen.getByText(/not ranked against one another/)).toBeInTheDocument();
  });
});
