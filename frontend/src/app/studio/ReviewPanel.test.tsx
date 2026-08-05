import "@testing-library/jest-dom/vitest";

import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { Draft } from "@/lib/api";

import ReviewPanel, { lineageState } from "./ReviewPanel";

/* What the review screen says about a draft, and — the half that keeps being got wrong — which
 * kind of draft it thinks it is looking at.
 *
 * Studio carried `(draft.generation_stage ?? "historical")` and labelled a legacy row created a
 * second ago as pre-migration. That fallback is gone; the same conflation survived one layer
 * down, because `editorial === null` was read as "historical" and `_editorial_lineage` returns
 * null for *any* draft whose brief row is missing — including a run that failed at planning ten
 * seconds ago. These tests pin the five cases apart. */

afterEach(cleanup);

const EDITORIAL: NonNullable<Draft["editorial"]> = {
  brief: {
    id: 1,
    objective: "Help operators explain one checkout decision.",
    audience: "product operators responsible for checkout",
    desired_action: "review one unnecessary checkout step",
    constraints: ["under 200 words", "no first-person plural"],
    requested_mode: "deep",
    recommended_mode: "light",
    research_mode: "deep",
    mode_signals: ["number", "organisation"],
    prompt: { name: "editorial.brief", version: "1.0.0" },
  },
  angle: {
    id: 2,
    thesis: "the checkout change is worth studying",
    tension: "small flow changes are easy to dismiss",
    audience_stake: "operators can inspect a concrete decision",
    cta: "review one field in your checkout",
    beats: ["state the change", "explain the implication"],
    prompt: { name: "editorial.angle_plan", version: "1.0.0" },
  },
  planned_claims: [
    { id: 10, text: "Acme removed one checkout field" },
    { id: 11, text: "the removal followed a support review" },
  ],
  research_job_id: 7,
  research: null,
  research_error: null,
  write_prompt: { name: "generation.editorial_write", version: "2.1.0" },
  prompts: [
    { name: "editorial.brief", version: "1.0.0", calls: 1 },
    { name: "research.queries", version: "1.0.0", calls: 1 },
    { name: "revision.targeted", version: "1.0.0", calls: 3 },
  ],
  correlation_id: "c0ffee-1234",
};

function draft(overrides: Partial<Draft> = {}): Draft {
  return {
    id: 42,
    idea: "What changed in Acme checkout?",
    mode: "directed",
    hook_text: "One field was doing no work.",
    body_text: "Body.",
    full_text: "One field was doing no work.\n\nBody.",
    visual_values: {},
    asset_values: {},
    visual_error: null,
    visual_png: null,
    has_previous_visual: false,
    zernio_post_id: null,
    revision: 1,
    pushed_revision: null,
    lineage: { hook: null, structure: null, visual: null },
    generation_stage: "ready",
    generation_error: null,
    gate_results: [],
    readiness_result: null,
    verification_result: null,
    revision_rounds: 0,
    editorial: null,
    ...overrides,
  };
}

describe("which editorial record a draft has", () => {
  /* The three the slice was asked for, plus the two that fall out of the same field and are
   * the reason a boolean would not do. */

  it("calls a pre-migration row historical, and only that", () => {
    // The six drafts in the live database, exactly: ready, no lineage, nothing failed.
    expect(lineageState(draft())).toEqual({ kind: "historical" });
    render(<ReviewPanel draft={draft()} />);
    expect(screen.getByText(/predates the workflow/)).toBeInTheDocument();
  });

  it("calls a legacy unreviewed row unreviewed, not historical", () => {
    // A `POST /drafts` row created a second ago. It also has no editorial lineage, so the
    // stage has to be tested first or this reads as pre-migration — which is the exact
    // mislabelling the removed `?? "historical"` fallback used to produce.
    const legacy = draft({ generation_stage: "unreviewed" });

    expect(lineageState(legacy)).toEqual({ kind: "unreviewed" });
    render(<ReviewPanel draft={legacy} />);
    expect(screen.getByText(/deprecated/)).toBeInTheDocument();
    expect(screen.getByText(/cannot be pushed/)).toBeInTheDocument();
    expect(screen.queryByText(/predates the workflow/)).not.toBeInTheDocument();
  });

  it("shows the complete lineage when there is one", () => {
    const full = draft({ editorial: EDITORIAL });

    expect(lineageState(full)).toEqual({ kind: "workflow", editorial: EDITORIAL });
    render(<ReviewPanel draft={full} />);
    expect(screen.getByText(EDITORIAL.brief.objective)).toBeInTheDocument();
    expect(screen.queryByText(/predates the workflow/)).not.toBeInTheDocument();
  });

  it("does not call a run that failed at planning a historical draft", () => {
    /* The live defect this test was written for. A below-floor refusal the door did not
     * catch, an unreadable model answer or `UnplannableClaims` all leave `editorial: null`
     * on a draft written moments ago, and the panel called every one of them pre-migration. */
    const stopped = draft({
      generation_stage: "failed",
      generation_error: "planning: ModeBelowFloor: this brief needs at least 'light' research",
    });

    expect(lineageState(stopped)).toEqual({ kind: "stopped_early" });
    render(<ReviewPanel draft={stopped} />);
    expect(screen.getByText(/stopped before the brief was written/)).toBeInTheDocument();
    expect(screen.getByText(/not an old one/)).toBeInTheDocument();
    expect(screen.queryByText(/predates the workflow/)).not.toBeInTheDocument();
    // The error is the whole account of it, so it has to be on screen.
    expect(screen.getByRole("alert")).toHaveTextContent("ModeBelowFloor");
  });

  it("says a run still planning has not written its brief yet", () => {
    const running = draft({ generation_stage: "planning" });

    expect(lineageState(running)).toEqual({ kind: "running" });
    render(<ReviewPanel draft={running} />);
    expect(screen.getByText(/have not been written yet/)).toBeInTheDocument();
    expect(screen.queryByText(/predates the workflow/)).not.toBeInTheDocument();
  });
});

describe("the research depth, reported three ways", () => {
  it("shows what was asked, the floor, what ran and what the detector saw", () => {
    render(<ReviewPanel draft={draft({ editorial: EDITORIAL })} />);

    // All four, and all four separately: one field would make "asked for deep, ran light"
    // unanswerable, which is why `ResearchJob` stores two columns and the brief stores three.
    expect(screen.getByText(/deep — multi-query investigation/)).toBeInTheDocument();
    expect(screen.getByText("Detected floor").nextSibling).toHaveTextContent("light");
    expect(screen.getByText("Ran as").nextSibling).toHaveTextContent("deep");
    expect(screen.getByText("number, organisation")).toBeInTheDocument();
    expect(screen.getByText(/raised above the floor deliberately/)).toBeInTheDocument();
  });

  it("reads no expressed depth as Auto and never as none", () => {
    /* `requested_mode: null` is "nobody asked"; `none` is "look nothing up". Printing one as
     * the other reports a request nobody made — and on a brief whose floor came out above it,
     * reads as a downgrade that did not happen. */
    const auto = {
      ...EDITORIAL,
      brief: {
        ...EDITORIAL.brief,
        requested_mode: null,
        recommended_mode: "light",
        research_mode: "light",
      },
    };
    render(<ReviewPanel draft={draft({ editorial: auto })} />);

    expect(screen.getByText(/Auto — no depth was asked for/)).toBeInTheDocument();
    expect(screen.queryByText(/raised above the floor/)).not.toBeInTheDocument();
  });

  it("says an empty signal list in words rather than leaving a blank", () => {
    const quiet = {
      ...EDITORIAL,
      brief: { ...EDITORIAL.brief, mode_signals: [], recommended_mode: "none" },
    };
    render(<ReviewPanel draft={draft({ editorial: quiet })} />);

    expect(screen.getByText(/nothing in this brief leaned on the outside world/)).toBeInTheDocument();
  });
});

describe("a measured zero is a number and an absence is an em dash", () => {
  it("prints a readiness score of zero as 0, not as —", () => {
    /* The trap this project keeps hitting, in the shape it takes here: `{points || "—"}` is
     * falsy at zero and draws an em dash over a rubric result that was actually computed. */
    const scored = draft({
      editorial: EDITORIAL,
      readiness_result: {
        rubric_version: "1.0.0",
        prompt_name: "rubric.readiness",
        prompt_version: "1.0.0",
        readiness_points: 0,
        decision: "needs_revision",
        summary: "Nothing in this draft survived the rubric.",
        deductions: [
          { criterion: "specificity", points: 60, reason: "no concrete example", evidence: "…" },
          { criterion: "clarity", points: 40, reason: "two arguments at once", evidence: "…" },
        ],
      },
    });
    render(<ReviewPanel draft={scored} />);

    const readiness = screen.getByText("Editorial readiness").parentElement!;
    expect(within(readiness).getByText("0")).toBeInTheDocument();
    expect(within(readiness).queryByText("—")).not.toBeInTheDocument();
    expect(within(readiness).getByText(/Nothing in this draft survived/)).toBeInTheDocument();
    // Every deduction, with its criterion, its points and its reason — the summary alone says
    // a draft failed and not what to change.
    expect(within(readiness).getByText(/no concrete example/)).toBeInTheDocument();
    expect(within(readiness).getByText(/two arguments at once/)).toBeInTheDocument();
  });

  it("distinguishes a rubric that never ran from a score of nothing", () => {
    render(<ReviewPanel draft={draft({ editorial: EDITORIAL })} />);

    expect(screen.getByText(/never ran/)).toBeInTheDocument();
    expect(screen.getByText(/not a score of zero/)).toBeInTheDocument();
  });

  it("prints zero revision rounds rather than hiding the line", () => {
    // It used to render only above zero, which left "clean, no revision needed" and "nothing
    // recorded a count" looking identical: an absent line.
    render(<ReviewPanel draft={draft({ editorial: EDITORIAL })} />);

    expect(screen.getByText(/0 bounded revision rounds/)).toBeInTheDocument();
  });

  it("tells a draft nobody verified from one verified to find nothing", () => {
    /* `null` and `{assertions: []}` are two answers. `?? []` collapses them and reports the
     * first as the second — a post nobody checked, presented as a post with nothing to check. */
    const { unmount } = render(<ReviewPanel draft={draft({ editorial: EDITORIAL })} />);
    expect(screen.getByText(/Nobody verified this draft/)).toBeInTheDocument();
    unmount();

    render(
      <ReviewPanel
        draft={draft({
          editorial: EDITORIAL,
          verification_result: {
            prompt_name: "verification.assertions",
            prompt_version: "1.0.0",
            basis: "idea",
            summary: "Nothing in the post asserts anything external.",
            assertions: [],
          },
        })}
      />,
    );
    expect(screen.queryByText(/Nobody verified this draft/)).not.toBeInTheDocument();
    expect(screen.getByText(/found nothing the post asserts/)).toBeInTheDocument();
  });
});

describe("what the review shows that had nowhere to render", () => {
  it("shows the brief's constraints, the angle's beats and the planned claims in order", () => {
    render(<ReviewPanel draft={draft({ editorial: EDITORIAL })} />);

    expect(screen.getByText("under 200 words")).toBeInTheDocument();
    expect(screen.getByText("state the change")).toBeInTheDocument();
    // In the order the plan stated them, and re-sorted by nothing — a list in an order implies
    // the order means something.
    const claims = screen.getAllByRole("listitem").map((item) => item.textContent);
    expect(claims.indexOf("Acme removed one checkout field")).toBeLessThan(
      claims.indexOf("the removal followed a support review"),
    );
  });

  it("names every prompt that ran, including the two no artifact carries", () => {
    /* `research.queries` and `revision.targeted` write no row with prompt columns on it, so
     * the four inline prompts cannot cover them. Drop `prompts` from the lineage and this is
     * what fails. */
    render(<ReviewPanel draft={draft({ editorial: EDITORIAL })} />);

    expect(screen.getByText(/research\.queries 1\.0\.0/)).toBeInTheDocument();
    expect(screen.getByText(/revision\.targeted 1\.0\.0/)).toBeInTheDocument();
    // The call count is a measurement, not a score: three calls is a loop that ran three times.
    expect(screen.getByText(/3 calls/)).toBeInTheDocument();
    expect(screen.getByText(/generation\.editorial_write 2\.1\.0/)).toBeInTheDocument();
  });

  it("shows the correlation id in full, because it is what a run is looked up by", () => {
    render(<ReviewPanel draft={draft({ editorial: EDITORIAL })} />);

    expect(screen.getByText("c0ffee-1234")).toBeInTheDocument();
    expect(screen.getByText("#7")).toBeInTheDocument();
  });

  it("shows each gate finding with its gate name", () => {
    render(
      <ReviewPanel
        draft={draft({
          editorial: EDITORIAL,
          generation_stage: "failed_review",
          gate_results: [{ gate: "uncited_claim", detail: "no citation supports the 40% figure" }],
        })}
      />,
    );

    expect(screen.getByText(/\[uncited_claim\] no citation supports/)).toBeInTheDocument();
  });

  it("badges an assertion on whether it blocks, not on what kind it is", () => {
    /* A `factual` assertion that passed is not a warning and an `opinion` needs no citation at
     * all — colouring by `kind` would tint the sentence rather than the finding. */
    render(
      <ReviewPanel
        draft={draft({
          editorial: EDITORIAL,
          verification_result: {
            prompt_name: "verification.assertions",
            prompt_version: "1.0.0",
            basis: "dossier",
            summary: "One assertion has nothing behind it.",
            assertions: [
              {
                text: "Acme removed one checkout field",
                kind: "factual",
                verdict: "supported",
                evidence: "claim C1",
                blocks: false,
              },
              {
                text: "activation rose by a third",
                kind: "factual",
                verdict: "uncited",
                evidence: "nothing in the dossier bears on it",
                blocks: true,
              },
            ],
          },
        })}
      />,
    );

    expect(screen.getByText("supported")).toBeInTheDocument();
    expect(screen.getByText("uncited")).toBeInTheDocument();
    expect(screen.getByText(/nothing in the dossier bears on it/)).toBeInTheDocument();
  });
});
