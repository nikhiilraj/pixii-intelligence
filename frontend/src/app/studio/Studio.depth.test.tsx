import "@testing-library/jest-dom/vitest";

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { STAGES, type Draft, type GenerationStage, type Template } from "@/lib/api";

import { DEPTH_LABEL, modePayload } from "@/components/research-depth";

import Studio, { draftPayload, templateId, NONE } from "./Studio";

/* The research-depth control, which is the half of this slice that changes what is *sent*.
 *
 * `IdeaIn.research_mode` has existed on the backend for as long as the workflow has, and this
 * page had never sent it: every draft ever generated here ran at whatever depth the floor
 * detector happened to pick, and an operator who knew a subject was contested had no way to say
 * so. Three properties are worth pinning, and only the first is about rendering:
 *
 * - Auto sends **no key**, and specifically not `"none"` — those are different requests and the
 *   second is refused on any factual idea;
 * - a chosen depth reaches the body of both spending routes;
 * - a refusal is answered on the page with the three facts the API returned, not flattened into
 *   the toast every other failure goes to.
 *
 * Radix Select cannot be driven in jsdom — see `Studio.test.tsx`'s header — so the depth is
 * changed here through the one control that sets it without opening a listbox: the raise button
 * on the refusal notice. That is not a workaround, it is the path a reader actually takes after
 * being refused, and asserting on the *next request body* proves the state moved rather than
 * proving a label changed. */

const toastError = vi.hoisted(() => vi.fn());
vi.mock("sonner", () => ({ toast: { error: toastError, success: vi.fn() } }));

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  toastError.mockReset();
});

const DRAFT: Draft = {
  id: 11,
  idea: "What changed in Acme checkout?",
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
  generation_stage: "planning",
  generation_error: null,
  gate_results: [],
  readiness_result: null,
  verification_result: null,
  revision_rounds: 0,
  editorial: null,
};

/** The 409 all three depth-taking routes answer a below-floor request with. */
const REFUSAL = {
  detail: {
    error: "this brief needs at least 'light' research (number, organisation); 'none' was requested",
    requested_mode: "none",
    recommended_mode: "light",
    mode_signals: ["number", "organisation"],
  },
};

function templates(): Template[] {
  return (["hook", "structure", "visual"] as const).map((kind, index) => ({
    id: index + 1,
    family_id: `fam-${kind}`,
    version: 1,
    kind,
    name: kind,
    status: "approved" as const,
    body: {},
    slots: [],
    provenance: [],
    notes: "",
  }));
}

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

/** Answers `/drafts/workflow` and `/drafts/variants` with whatever the test queued for them. */
function stub(...answers: Response[]) {
  const queue = [...answers];
  // The parameters are declared even though the body ignores them: `mock.calls` is typed off
  // this signature, and a `() => …` stub gives every recorded call the type `[]` — so reading
  // the request body back out stops compiling.
  const fetchStub = vi.fn((_url: string, _init?: RequestInit) =>
    Promise.resolve(queue.length > 1 ? queue.shift()! : queue[0]),
  );
  vi.stubGlobal("fetch", fetchStub);
  return fetchStub;
}

/** The **most recent** post to `path`. Not the first: the raise test presses Generate twice on
 *  purpose, and the whole claim is about what the second one carried. */
function bodyOf(fetchStub: ReturnType<typeof stub>, path: string): Record<string, unknown> {
  const call = fetchStub.mock.calls.findLast(([url]) => String(url).endsWith(path));
  if (!call) throw new Error(`nothing was posted to ${path}`);
  return JSON.parse(String(call[1]?.body));
}

function open() {
  render(<Studio assets={[]} drafts={[]} templates={templates()} />);
  fireEvent.change(screen.getByPlaceholderText(/what is this post about/i), {
    target: { value: "What changed in Acme checkout?" },
  });
}

describe("the depth on the way out", () => {
  it("sends no research_mode at all on Auto", () => {
    /* `research_mode: null` and an absent key mean the same thing to `IdeaIn`; `"none"` does
     * not. Auto is "nobody expressed a preference", and sending `none` for it would make the
     * default state of a dropdown a request that a factual idea refuses. */
    const body = draftPayload(
      "a nine figure exit",
      { hook: templateId(NONE), structure: templateId(NONE), visual: templateId(NONE) },
      undefined,
      {},
      "auto",
    );

    expect(body).not.toHaveProperty("research_mode");
    expect(modePayload("auto")).toEqual({});
  });

  it("sends the chosen depth by name", () => {
    expect(modePayload("deep")).toEqual({ research_mode: "deep" });
    expect(modePayload("none")).toEqual({ research_mode: "none" });
  });

  it("posts a Generate with no depth key while the control is on Auto", async () => {
    const fetchStub = stub(json(201, DRAFT));
    open();
    fireEvent.click(screen.getByRole("button", { name: /generate draft/i }));

    await waitFor(() => expect(fetchStub).toHaveBeenCalled());
    expect(bodyOf(fetchStub, "/drafts/workflow")).not.toHaveProperty("research_mode");
  });

  it("labels every option by what it does and never as recommended", () => {
    // The control is inherently ordered — `DEPTH` exists for the floor comparison — and the
    // wording is what keeps it from reading as a ranking. "Auto" must not read as the good one.
    expect(Object.values(DEPTH_LABEL).join(" ")).not.toMatch(/recommend|best|top/i);
    expect(DEPTH_LABEL.deep).toMatch(/multi-query/);
  });
});

describe("a depth below the idea's floor", () => {
  it("is explained on the page with what was asked, the floor and what it saw", async () => {
    const fetchStub = stub(json(409, REFUSAL));
    open();
    fireEvent.click(screen.getByRole("button", { name: /generate draft/i }));

    const notice = await screen.findByRole("alert");
    expect(notice).toHaveTextContent("none");
    expect(notice).toHaveTextContent("light");
    // The signals, which are the answer to the only question a surprising floor raises. They
    // survive only because the API sends them as a field: `messageFrom` flattens the object
    // into prose, so recovering them from `message` would mean regexing a sentence.
    expect(notice).toHaveTextContent("number, organisation");
    expect(notice).toHaveTextContent(/Nothing was written and nothing was billed/);
    // Not the toast. Every other failure goes there; this one has a control as its remedy.
    expect(toastError).not.toHaveBeenCalled();
    expect(fetchStub).toHaveBeenCalledOnce();
  });

  it("raises the control to the floor, and the next request carries it", async () => {
    const fetchStub = stub(json(409, REFUSAL), json(201, DRAFT));
    open();
    fireEvent.click(screen.getByRole("button", { name: /generate draft/i }));

    fireEvent.click(await screen.findByRole("button", { name: /set the depth to light/i }));
    // The notice goes with the state it describes — a refusal left standing over a depth that
    // has since changed describes a request nobody would make now.
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /generate draft/i }));
    await waitFor(() => expect(fetchStub).toHaveBeenCalledTimes(2));
    expect(bodyOf(fetchStub, "/drafts/workflow")).toMatchObject({ research_mode: "light" });
  });

  it("clears when the idea changes, because the floor is a function of the idea", async () => {
    stub(json(409, REFUSAL));
    open();
    fireEvent.click(screen.getByRole("button", { name: /generate draft/i }));
    await screen.findByRole("alert");

    fireEvent.change(screen.getByPlaceholderText(/what is this post about/i), {
      target: { value: "why clearer product writing matters" },
    });

    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("carries the depth into a variants batch too", async () => {
    const fetchStub = stub(json(409, REFUSAL), json(201, { variants: [], llm_calls: 0, image_calls: 0, search_calls: 0 }));
    open();
    fireEvent.click(screen.getByRole("button", { name: /generate draft/i }));
    fireEvent.click(await screen.findByRole("button", { name: /set the depth to light/i }));

    fireEvent.click(screen.getByRole("button", { name: /write variants/i }));
    await waitFor(() => expect(fetchStub).toHaveBeenCalledTimes(2));

    const body = bodyOf(fetchStub, "/drafts/variants");
    // The idea and the depth, and still no templates: the route varies those itself. A batch
    // run at a different depth from the one on screen would be a control the page ignored.
    expect(body).toEqual({ idea: "What changed in Acme checkout?", research_mode: "light" });
  });

  it("leaves every other failure in the toast", async () => {
    // The refusal is recognised by the *shape* of `detail`, never by the status: 409 is also
    // what `NoUsableTemplates` answers with, and that one carries a plain string.
    stub(json(409, { detail: "approve at least one hook, structure and visual first" }));
    open();
    fireEvent.click(screen.getByRole("button", { name: /generate draft/i }));

    await waitFor(() => expect(toastError).toHaveBeenCalled());
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});

/* The Push gate, which is not about the depth and is here because this file is where a draft is
 * rendered in every stage. It read `stage === "failed" || stage === "failed_review"` — the
 * hand-written pair `reviewReady`'s own docstring says it exists to replace — and `unreviewed`
 * was in neither comparison. So the one state whose entire definition is that nothing vouched
 * for it offered a live Push, and the server answered 409: a button that is alive and then 409s
 * is a button that looks like it did something. */
describe("what may be pushed", () => {
  function pushButton(stage: GenerationStage) {
    cleanup();
    stub(json(201, DRAFT));
    render(
      <Studio
        assets={[]}
        drafts={[]}
        initialDraft={{ ...DRAFT, generation_stage: stage, full_text: "words" }}
        templates={templates()}
      />,
    );
    return screen.getByRole("button", { name: /push to zernio/i });
  }

  it("offers Push for a ready draft and for no other stage", () => {
    // Every stage, not a sample: the gate is a predicate over the whole enum, and a test that
    // checked three of eleven would pass against a fourth being added on the wrong side.
    for (const stage of STAGES) {
      expect([stage, pushButton(stage).hasAttribute("disabled")]).toEqual([
        stage,
        stage !== "ready",
      ]);
    }
  });

  it("offers Retry for the two failures and for nothing else", () => {
    /* The other predicate on the same row of buttons, and it was hand-written too. `unreviewed`
       is the case that matters: retrying a legacy row would start a workflow the row never had,
       against a stage `advance` refuses to move. Nothing pinned this until the Push gate next
       to it turned out to have drifted. */
    for (const stage of STAGES) {
      cleanup();
      stub(json(201, DRAFT));
      render(
        <Studio
          assets={[]}
          drafts={[]}
          initialDraft={{ ...DRAFT, generation_stage: stage, full_text: "words" }}
          templates={templates()}
        />,
      );
      const offered = screen.queryByRole("button", { name: /retry complete flow/i }) !== null;
      expect([stage, offered]).toEqual([stage, stage === "failed" || stage === "failed_review"]);
    }
  });

  it("refuses a legacy unreviewed row, which the workflow block says cannot be pushed", () => {
    // The contradiction this pins: the panel states the arrangement in prose and the control
    // has to agree with it. `ready` stays live, which is what keeps the six pre-migration
    // drafts in the live database pushable.
    expect(pushButton("unreviewed")).toBeDisabled();
    expect(pushButton("ready")).toBeEnabled();
  });
});
