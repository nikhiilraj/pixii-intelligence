import "@testing-library/jest-dom/vitest";

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import ScoreboardPage from "./page";

const base = {
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
};

function stubFetch(rows: Record<string, unknown>[]) {
  vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(new Response(JSON.stringify(rows), { status: 200, headers: { "Content-Type": "application/json" } }))));
}

afterEach(() => { vi.unstubAllGlobals(); cleanup(); });

describe("the evidence-first scoreboard", () => {
  it("separates observed versions from measured zeroes", async () => {
    stubFetch([
      { ...base, family: "observed", name: "observed-shape", sample_count: 3 },
      { ...base, family: "absent", name: "never-used" },
    ]);
    render(await ScoreboardPage());
    expect(screen.getByRole("heading", { name: "1 version with posts" })).toBeVisible();
    expect(screen.getByRole("heading", { name: "1 version with none" })).toBeVisible();
    expect(screen.getByLabelText("3 published posts")).toHaveTextContent("n=3");
    expect(screen.getByText("a measured zero, not a gap")).toBeVisible();
  });

  it("states that ordering is alphabetical and never ranked", async () => {
    stubFetch([{ ...base, sample_count: 2 }]);
    render(await ScoreboardPage());
    expect(screen.getByText(/alphabetical · not ranked · no averages/)).toBeVisible();
    expect(screen.getByText(/Versions are alphabetical, never ranked/)).toBeVisible();
  });

  it("withholds averages and explains the reading threshold", async () => {
    stubFetch([{ ...base, sample_count: 3, mean_engaged_actions: 99 }]);
    render(await ScoreboardPage());
    expect(screen.queryByText("99")).not.toBeInTheDocument();
    expect(screen.getByText(/withholds averages/)).toBeVisible();
    expect(screen.getByText(/Five posts is where a figure starts being worth reading/)).toBeVisible();
  });
});
