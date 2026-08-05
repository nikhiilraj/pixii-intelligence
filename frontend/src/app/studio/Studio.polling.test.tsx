import "@testing-library/jest-dom/vitest";

import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Draft, GenerationStage, Template } from "@/lib/api";

import Studio from "./Studio";

/* What this file is about: the run happens somewhere else now.
 *
 * `POST /drafts/workflow` answers at `planning` and a background worker commits each stage as
 * it enters it, so the page's job is to ask where the run has got to and to say so honestly —
 * and to stop asking the moment it stops moving. jsdom cannot prove any of this looks right;
 * what it can prove is which requests were made, when they stopped, and what the page claims
 * while they are in flight.
 *
 * Timers are faked and advanced explicitly. A polling test that waits on wall-clock time is a
 * test that passes on a fast machine and fails on a loaded one, which is worse than none. */

const toastError = vi.hoisted(() => vi.fn());
vi.mock("sonner", () => ({ toast: { error: toastError, success: vi.fn() } }));

const POLL_MS = 1000;

function draft(stage: GenerationStage, overrides: Partial<Draft> = {}): Draft {
  return {
    id: 42,
    idea: "one clear point",
    mode: "directed",
    hook_text: "",
    body_text: "",
    full_text: "",
    visual_values: {},
    asset_values: {},
    visual_error: null,
    visual_png: null,
    has_previous_visual: false,
    zernio_post_id: null,
    revision: 1,
    pushed_revision: null,
    lineage: { hook: null, structure: null, visual: null },
    generation_stage: stage,
    generation_error: null,
    gate_results: [],
    verification_result: null,
    readiness_result: null,
    revision_rounds: 0,
    // Overridable, and the in-flight cases below leave it null on purpose: a run at
    // `planning` has not written a brief yet, which `lineageState` must not read as
    // pre-migration.
    editorial: null,
    ...overrides,
  };
}

const READY = draft("ready", {
  hook_text: "A 9-figure exit.",
  body_text: "One main image did it.",
  full_text: "A 9-figure exit.\n\nOne main image did it.",
});

function templates(): Template[] {
  return (["hook", "structure", "visual"] as const).map((kind, index) => ({
    id: index + 1,
    family_id: `fam-${kind}`,
    version: 1,
    kind,
    name: `a ${kind}`,
    status: "approved" as const,
    body: { renderer: "html" },
    slots: [],
    provenance: [],
    notes: "",
  }));
}

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

/** A run, as a sequence of stages: the first is what the start answers with (and what an
 *  `initialDraft` should be), and each poll moves one along.
 *
 *  The last stage is repeated once the sequence runs out, so a test that keeps advancing the
 *  clock past the end gets the terminal answer again rather than an undefined one. That is also
 *  what makes "it stopped asking" checkable — the stub would happily go on answering.
 */
function stubRun(...stages: Draft[]) {
  const queue = [...stages];
  const polls: string[] = [];
  const fetchStub = vi.fn((url: string, init?: RequestInit) => {
    const path = String(url).replace(/^https?:\/\/[^/]+/, "");
    if (init?.method === "POST") return Promise.resolve(jsonResponse(201, queue[0]));
    polls.push(path);
    if (queue.length > 1) queue.shift();
    return Promise.resolve(jsonResponse(200, queue[0]));
  });
  vi.stubGlobal("fetch", fetchStub);
  return { fetchStub, polls };
}

/** Advance the fake clock and let everything it started settle.
 *
 *  Explicit rather than `waitFor`/`findBy`: React Testing Library only auto-advances *jest*
 *  fake timers — it detects them through a `jest` global that does not exist here — so every
 *  async utility in this file would sit waiting on a clock nothing is moving. Advancing by
 *  hand is also the stronger test: "one poll happened after one second" is an assertion, where
 *  "the text turned up eventually" is a race with a passing grade.
 *
 *  `times = 0` flushes the promises an already-fired request left behind without moving the
 *  clock, which is what a click needs. */
function tick(times = 1) {
  return act(() => vi.advanceTimersByTimeAsync(POLL_MS * times));
}

beforeEach(() => {
  vi.useFakeTimers();
  window.history.replaceState(null, "", "/studio");
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  toastError.mockReset();
  cleanup();
});

describe("a run that is happening somewhere else", () => {
  it("answers at planning and says what that means, rather than showing an empty post", async () => {
    stubRun(draft("planning"));
    render(<Studio templates={templates()} assets={[]} drafts={[]} />);

    fireEvent.change(screen.getByPlaceholderText(/what is this post about/i), {
      target: { value: "one clear point" },
    });
    fireEvent.click(screen.getByRole("button", { name: /generate draft/i }));
    await tick(0);

    expect(screen.getByText(/Writing the brief and planning the angle/)).toBeVisible();
    // The article is not rendered at all. An empty bordered box reads as a post that says
    // nothing, which is a different claim from a post that does not exist yet.
    expect(screen.queryByRole("article")).toBeNull();
  });

  it("puts the draft in the address bar, so a reload finds the attempt", async () => {
    stubRun(draft("planning"));
    render(<Studio templates={templates()} assets={[]} drafts={[]} />);

    fireEvent.change(screen.getByPlaceholderText(/what is this post about/i), {
      target: { value: "one clear point" },
    });
    fireEvent.click(screen.getByRole("button", { name: /generate draft/i }));
    await tick(0);

    expect(window.location.search).toBe("?draft=42");
  });

  it("reports each stage the run reaches", async () => {
    stubRun(draft("planning"), draft("researching"), draft("drafting"), draft("verifying"));
    render(
      <Studio
        templates={templates()}
        assets={[]}
        drafts={[]}
        initialDraft={draft("planning")}
      />,
    );

    expect(screen.getByText(/Writing the brief and planning the angle/)).toBeVisible();
    await tick();
    expect(screen.getByText(/Searching and reading sources/)).toBeVisible();
    await tick();
    expect(screen.getByText(/Writing the post from the plan/)).toBeVisible();
    await tick();
    expect(screen.getByText(/Checking every claim against the sources/)).toBeVisible();
  });

  it("recovers a run in flight on a reload, with nothing clicked", async () => {
    const { polls } = stubRun(draft("researching"), draft("ready"));
    render(
      <Studio
        templates={templates()}
        assets={[]}
        drafts={[]}
        initialDraft={draft("researching")}
      />,
    );

    expect(screen.getByText(/Searching and reading sources/)).toBeVisible();
    await tick();

    expect(polls).toEqual(["/drafts/42"]);
  });

  it("shows the finished post and stops asking", async () => {
    const { polls } = stubRun(draft("rendering"), READY);
    render(
      <Studio
        templates={templates()}
        assets={[]}
        drafts={[]}
        initialDraft={draft("rendering")}
      />,
    );

    expect(screen.getByText(/Drawing the visual/)).toBeVisible();
    await tick();
    expect(screen.getByText(/One main image did it/)).toBeVisible();

    const asked = polls.length;
    await tick(5);
    // The interval is torn down on a terminal stage. Without that, a finished draft would be
    // re-fetched once a second for as long as the tab stayed open.
    expect(polls.length).toBe(asked);
  });

  it("stops asking when the run fails, and says why", async () => {
    const failed = draft("failed", {
      generation_error:
        "the run stopped responding at researching and was declared failed after 15 minutes.",
    });
    const { polls } = stubRun(draft("researching"), failed);
    render(
      <Studio
        templates={templates()}
        assets={[]}
        drafts={[]}
        initialDraft={draft("researching")}
      />,
    );

    await tick();
    expect(screen.getByRole("alert")).toHaveTextContent(/stopped responding at researching/);
    // A failure a person can act on: the reason, and the control that starts a fresh attempt.
    expect(screen.getByRole("button", { name: /retry complete flow/i })).toBeEnabled();

    const asked = polls.length;
    await tick(5);
    expect(polls.length).toBe(asked);
  });

  it("leaks no timer across an unmount", async () => {
    const { polls } = stubRun(draft("drafting"));
    const view = render(
      <Studio
        templates={templates()}
        assets={[]}
        drafts={[]}
        initialDraft={draft("drafting")}
      />,
    );

    await tick();
    const asked = polls.length;
    view.unmount();
    await tick(5);

    // `page.tsx` keys this component on the requested id, so navigating between drafts
    // unmounts it mid-run. A surviving interval would poll a draft nobody is looking at.
    expect(polls.length).toBe(asked);
  });
});

describe("the client half of duplicate protection", () => {
  it("will not let Generate be pressed while a run is in flight", async () => {
    stubRun(draft("drafting"));
    render(
      <Studio
        templates={templates()}
        assets={[]}
        drafts={[]}
        initialDraft={draft("drafting")}
      />,
    );
    fireEvent.change(screen.getByPlaceholderText(/what is this post about/i), {
      target: { value: "one clear point" },
    });

    // The server is the authority — the claim is a UNIQUE index and a second press is answered
    // with the running draft — so this is a disabled control, not a refusal.
    expect(screen.getByRole("button", { name: /generating/i })).toBeDisabled();
  });

  it("lets Generate be pressed again once the run has finished", async () => {
    stubRun(READY);
    render(<Studio templates={templates()} assets={[]} drafts={[]} initialDraft={READY} />);
    fireEvent.change(screen.getByPlaceholderText(/what is this post about/i), {
      target: { value: "another point" },
    });

    expect(screen.getByRole("button", { name: /generate draft/i })).toBeEnabled();
  });
});

describe("what the page does with a failed poll", () => {
  it("keeps the stage it has rather than announcing a failure the run did not have", async () => {
    const fetchStub = vi.fn((url: string, init?: RequestInit) =>
      init?.method === "POST"
        ? Promise.resolve(jsonResponse(201, draft("planning")))
        : Promise.reject(new Error("network down")),
    );
    vi.stubGlobal("fetch", fetchStub);
    render(
      <Studio
        templates={templates()}
        assets={[]}
        drafts={[]}
        initialDraft={draft("drafting")}
      />,
    );

    await tick(3);

    // The run is unaffected by whether this browser could reach the API for a second, and a
    // toast per second on a flaky connection would bury the page. The stage simply stops
    // advancing, which is the truth.
    expect(toastError).not.toHaveBeenCalled();
    expect(screen.getByText(/Writing the post from the plan/)).toBeVisible();
  });
});
