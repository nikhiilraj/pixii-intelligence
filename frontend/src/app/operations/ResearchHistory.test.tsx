import "@testing-library/jest-dom/vitest";

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { ApiResult, Dossier, ResearchJobSummary } from "@/lib/api";

import { ResearchHistory } from "./ResearchHistory";

/* The failure-versus-empty boundary, which is the reason `ApiResult` is a three-way union and
 * the reason this list is not `jobs ?? []`.
 *
 * "No research has ever run" is a strong claim, and a page that makes it off a request that
 * did not arrive is lying with confidence. Both directions are asserted throughout: that the
 * failure notice appears, *and* that the empty-list copy does not — a component that always
 * showed the notice would pass the first half of every one of these.
 */

function jobs(rows: ResearchJobSummary[]): ApiResult<ResearchJobSummary[]> {
  return { ok: true, data: rows };
}

const RUN: ResearchJobSummary = {
  job_id: 4,
  question: "How fast is agentic coding adoption?",
  mode: "light",
  recommended_mode: "light",
  state: "completed",
  researched_at: "2026-08-04T10:05:00",
};

const DOSSIER: Dossier = {
  job_id: 4,
  question: "How fast is agentic coding adoption?",
  mode: "light",
  recommended_mode: "light",
  mode_signals: ["a statistic was planned"],
  state: "completed",
  researched_at: "2026-08-04T10:05:00",
  freshness_days: 30,
  sources: [],
  claims: [],
  citations: [],
  unknowns: [],
  contradictions: [],
  spend: {
    queries: 2,
    sources_found: 5,
    sources_fetched: 3,
    llm_calls: 2,
    budget_exhausted: null,
  },
};

afterEach(cleanup);

describe("a failed read is not an empty list", () => {
  it("reports an HTTP failure and does not claim no research exists", () => {
    render(
      <ResearchHistory
        jobs={{ ok: false, kind: "http", status: 500, message: "the database went away" }}
        dossier={null}
        selected={null}
      />,
    );

    expect(screen.getByRole("alert")).toHaveTextContent("the database went away");
    // The other direction, and the one that actually discriminates.
    expect(screen.queryByText("No research run has been recorded.")).not.toBeInTheDocument();
  });

  it("reports an unreachable backend as unreachable", () => {
    render(
      <ResearchHistory
        jobs={{ ok: false, kind: "network", message: "fetch failed" }}
        dossier={null}
        selected={null}
      />,
    );

    expect(screen.getByText(/could not be reached|make api/i)).toBeInTheDocument();
    expect(screen.queryByText("No research run has been recorded.")).not.toBeInTheDocument();
  });

  it("says an honestly empty list is empty, and says why it might be", () => {
    render(<ResearchHistory jobs={jobs([])} dossier={null} selected={null} />);

    expect(screen.getByText("No research run has been recorded.")).toBeInTheDocument();
    expect(screen.getByText(/honestly empty list, not a failure/)).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});

describe("the list", () => {
  it("keeps the order the route returned, which is a chronology and not a ranking", () => {
    const rows = [
      { ...RUN, job_id: 9, question: "newest" },
      { ...RUN, job_id: 4, question: "middle" },
      { ...RUN, job_id: 1, question: "oldest" },
    ];
    render(<ResearchHistory jobs={jobs(rows)} dossier={null} selected={null} />);

    // Rendered as given. Sorting by state, by coverage or by anything else is the never-rank
    // rule applied to evidence — `research.dossier()` refuses the same thing one level down.
    expect([...screen.getAllByRole("listitem")].map((li) => li.textContent)).toEqual([
      expect.stringContaining("newest"),
      expect.stringContaining("middle"),
      expect.stringContaining("oldest"),
    ]);
  });

  it("shows a failed run as failed rather than collapsing it into the list", () => {
    render(
      <ResearchHistory
        jobs={jobs([{ ...RUN, state: "failed" }])}
        dossier={null}
        selected={null}
      />,
    );

    // `GET /research`'s own docstring: an empty list reads as "no research exists", and a
    // failed row is the run that died. Collapsing the two makes the failure invisible in the
    // one place someone goes looking for it.
    expect(screen.getByText("failed")).toBeInTheDocument();
  });

  it("shows both the mode that ran and the floor that was detected", () => {
    render(
      <ResearchHistory
        jobs={jobs([{ ...RUN, mode: "none", recommended_mode: "deep" }])}
        dossier={null}
        selected={null}
      />,
    );

    // One field would make "the system asked for deep and the run did none" unanswerable.
    expect(screen.getByText(/mode none · floor deep/)).toBeInTheDocument();
  });

  it("links each run by id and lets the selected one be unselected", () => {
    render(<ResearchHistory jobs={jobs([RUN])} dossier={null} selected={null} />);
    expect(screen.getByRole("link")).toHaveAttribute("href", "/operations?job=4");

    cleanup();
    render(<ResearchHistory jobs={jobs([RUN])} dossier={{ ok: true, data: DOSSIER }} selected={4} />);
    expect(screen.getByRole("link")).toHaveAttribute("href", "/operations");
  });
});

describe("the selected dossier", () => {
  it("renders the shared panel rather than a second implementation of it", () => {
    render(<ResearchHistory jobs={jobs([RUN])} dossier={{ ok: true, data: DOSSIER }} selected={4} />);

    // Asserted on furniture only `DossierPanel` draws — the run header and the claim-coverage
    // band. The question alone would not discriminate: the list row above carries it too.
    expect(screen.getByText("Claim coverage")).toBeInTheDocument();
    expect(screen.getByText("Freshness policy")).toBeInTheDocument();
    // And its wording for an empty claim list, which is a sentence rather than four zeros.
    expect(screen.getByText(/Either the claim pass proposed none or it never ran/)).toBeInTheDocument();
    expect(screen.getAllByText("How fast is agentic coding adoption?")).toHaveLength(2);
  });

  it("does not carry Studio's wording about a run linked to a draft", () => {
    render(<ResearchHistory jobs={jobs([RUN])} dossier={{ ok: true, data: DOSSIER }} selected={4} />);

    // The reason only the inner panel is shared: `ResearchPanel`'s empty state says a run is
    // "never selected from the URL", which is exactly what this screen does.
    expect(screen.queryByText(/never selected from the URL/)).not.toBeInTheDocument();
    expect(screen.queryByText(/No research run is linked to this draft/)).not.toBeInTheDocument();
  });

  it("renders a run that could not be opened as a refusal, not as no selection", () => {
    render(
      <ResearchHistory
        jobs={jobs([RUN])}
        dossier={{ ok: false, kind: "http", status: 404, message: "no research job 99" }}
        selected={99}
      />,
    );

    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("no research job 99");
    expect(alert).toHaveTextContent("a job id can be typed into the address bar");
  });
});
