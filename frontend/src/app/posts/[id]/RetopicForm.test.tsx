import "@testing-library/jest-dom/vitest";

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import RetopicForm from "./RetopicForm";

/* US-014's post half. Two things here can be wrong without anything looking wrong:
 *
 *   1. the request body. `RetopicIn` (api_drafts.py:206) carries two optional ids and refuses
 *      anything but exactly one of them — but sending this page's integer as `source_draft_id`
 *      would NOT be refused. It would re-topic draft 95, a different, existing row, and answer
 *      201 with a draft whose lineage came from somewhere else entirely. Deep-equal on the
 *      parsed body is the only assertion that catches it.
 *   2. 409 vs 404. `_source_draft` answers 404 only for an id no post has, and 409 for a post
 *      this app did not generate as well as for a recorded template version that has since left
 *      the table. Reporting a 409 as "no such post" would tell the user their post is gone.
 *
 * And the third thing, which is money rather than truth: one press buys one chat completion and
 * one render. A second press buys a second one, so a press that is already in flight must not be
 * pressable.
 *
 * Per the frontend testing policy: our own logic only, and nothing here claims anything about
 * layout or contrast — this is jsdom. */

const toastError = vi.hoisted(() => vi.fn());
const toastSuccess = vi.hoisted(() => vi.fn());
vi.mock("sonner", () => ({ toast: { error: toastError, success: toastSuccess } }));

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

/** The route answers with the new draft plus what it spent, and the component reads both back
 *  off that body — a stub that returned `{ok: true}` would not exercise the path it takes. */
function retopicOut(overrides: Record<string, unknown> = {}) {
  return {
    id: 42,
    idea: "why keyword-dense titles stall on Walmart",
    mode: "assisted",
    hook_text: "",
    body_text: "",
    full_text: "",
    visual_values: {},
    asset_values: {},
    visual_error: null,
    visual_png: null,
    zernio_post_id: null,
    lineage: {
      hook: { family: "eb93614c", version: 1, name: "open-question-then-tested" },
      structure: { family: "e8742a6d", version: 2, name: "case-study-loop" },
      visual: { family: "a2bcf8e2", version: 1, name: "stat-hero" },
    },
    llm_calls: 1,
    image_calls: 1,
    ...overrides,
  };
}

function stubFetch(answer: Response | Error) {
  const spy = vi.fn<(url: string, init?: RequestInit) => Promise<Response>>(() =>
    answer instanceof Error ? Promise.reject(answer) : Promise.resolve(answer),
  );
  vi.stubGlobal("fetch", spy);
  return spy;
}

function sentBody(spy: ReturnType<typeof stubFetch>): unknown {
  return JSON.parse(String(spy.mock.calls[0]?.[1]?.body));
}

function ideaBox(): HTMLInputElement {
  return screen.getByLabelText(/^The new subject/) as HTMLInputElement;
}

function startButton(): HTMLButtonElement {
  return screen.getByRole("button", { name: /^Write the new draft/ }) as HTMLButtonElement;
}

function type(value: string) {
  fireEvent.change(ideaBox(), { target: { value } });
}

afterEach(() => {
  vi.unstubAllGlobals();
  toastError.mockClear();
  toastSuccess.mockClear();
  // Explicit because `globals: false` — see VerdictForm.test.tsx.
  cleanup();
});

describe("starting a re-topic from a post", () => {
  it("sends the new subject and the post's own id under the field names the route declares", async () => {
    const spy = stubFetch(jsonResponse(201, retopicOut()));
    render(<RetopicForm postId={95} />);

    // Typed with the whitespace a real paste carries. The button's disabled state is already
    // computed from the trimmed value; what was untested is that the trimmed value is also
    // what gets *sent* — the subject reaches the prompt, and leading newlines in a prompt are
    // not free.
    type("  why keyword-dense titles stall on Walmart  ");
    fireEvent.click(startButton());

    await waitFor(() => expect(spy).toHaveBeenCalled());
    expect(spy.mock.calls[0][0]).toContain("/drafts/retopic");
    expect(spy.mock.calls[0][1]?.method).toBe("POST");
    // Deep-equal, not `toMatchObject`: `source_draft_id` carrying this number would be accepted
    // and would re-topic a different row, and a body with both ids is a 422.
    expect(sentBody(spy)).toEqual({
      idea: "why keyword-dense titles stall on Walmart",
      source_post_id: 95,
    });
  });

  it("cannot be fired without a subject, so a stray click spends nothing", () => {
    const spy = stubFetch(jsonResponse(201, retopicOut()));
    render(<RetopicForm postId={95} />);

    expect(startButton()).toBeDisabled();
    fireEvent.click(startButton());
    // Whitespace is not a subject, and the route would happily generate against it.
    type("   ");
    expect(startButton()).toBeDisabled();
    fireEvent.click(startButton());

    expect(spy).not.toHaveBeenCalled();

    type("a real subject");
    expect(startButton()).toBeEnabled();
  });

  it("buys one completion per press — a second press while one is in flight is refused", async () => {
    const spy = vi.fn<(url: string, init?: RequestInit) => Promise<Response>>(
      () => new Promise<Response>(() => {}),
    );
    vi.stubGlobal("fetch", spy);
    render(<RetopicForm postId={95} />);

    type("why keyword-dense titles stall on Walmart");
    fireEvent.click(startButton());

    await waitFor(() => expect(startButton()).toBeDisabled());
    fireEvent.click(startButton());
    fireEvent.click(startButton());

    expect(spy).toHaveBeenCalledTimes(1);
  });
});

describe("the draft that comes back", () => {
  it("reports what it spent and links to it in the studio, without publishing anything", async () => {
    stubFetch(jsonResponse(201, retopicOut({ id: 42, llm_calls: 1, image_calls: 1 })));
    render(<RetopicForm postId={95} />);

    type("why keyword-dense titles stall on Walmart");
    fireEvent.click(startButton());

    // The spend is the app's first: there was no counter anywhere before this route, so a
    // number that is not shown is a number nobody has.
    await waitFor(() => expect(screen.getByText(/1 chat completion/)).toBeInTheDocument());
    expect(screen.getByText(/1 image render/)).toBeInTheDocument();
    // A draft is a draft. Nothing here pushed and nothing published.
    expect(screen.getByText(/nothing has been pushed or published/i)).toBeInTheDocument();

    const link = screen.getByRole("link", { name: /open draft 42/i });
    expect(link).toHaveAttribute("href", "/studio?draft=42");
    expect(toastSuccess).toHaveBeenCalled();
  });

  it("reports a spend of two calls as two, never as a fixed price", async () => {
    stubFetch(jsonResponse(201, retopicOut({ id: 43, llm_calls: 2, image_calls: 0 })));
    render(<RetopicForm postId={95} />);

    type("a second subject");
    fireEvent.click(startButton());

    await waitFor(() => expect(screen.getByText(/2 chat completions/)).toBeInTheDocument());
    expect(screen.getByText(/0 image renders/)).toBeInTheDocument();
  });
});

/* The distinction the backend went out of its way to make, held on the surface that shows it.
 * A 409 means the post exists and has no draft behind it (or its recorded template version has
 * left the table); a 404 means no post carries that id at all. Same colour toast, different
 * sentence, and the API's own `detail` under both so the two 409 sources stay apart. */
describe("a refused re-topic", () => {
  it("does not say the post is missing when the conflict is a missing draft", async () => {
    /* Refused on the *second* press, after a first one succeeded, because the spend report is
       the thing that must not survive: "Draft 42 written … 1 chat completion" still on screen
       under a refusal is a claim that money bought something, on the only surface in this app
       that reports money at all. */
    const answers = [
      jsonResponse(201, retopicOut()),
      jsonResponse(409, {
        detail: "post 95 was not generated here, so it records no templates to re-topic from",
      }),
    ];
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.resolve(answers.shift()!)),
    );
    render(<RetopicForm postId={95} />);

    type("why keyword-dense titles stall on Walmart");
    fireEvent.click(startButton());
    await waitFor(() => expect(screen.getByText(/1 chat completion/)).toBeInTheDocument());

    type("a second subject");
    fireEvent.click(startButton());

    await waitFor(() => expect(toastError).toHaveBeenCalled());
    // The standing explainer above the field also says "a chat completion" — what must be gone
    // is the counted one, which is the report of a press.
    expect(screen.queryByText(/1 chat completion/)).not.toBeInTheDocument();
    const [headline, options] = toastError.mock.calls[0];
    expect(headline).toMatch(/templates/i);
    expect(headline).not.toMatch(/not found|no such post|does not exist/i);
    expect(options).toEqual({
      description: "post 95 was not generated here, so it records no templates to re-topic from",
    });
    // Once, for the press that worked — the refusal added no second one.
    expect(toastSuccess).toHaveBeenCalledTimes(1);
    // And the link to the draft the first press wrote is gone with the rest of that report:
    // it is offered as the outcome of the press just made, not as a history.
    expect(screen.queryByRole("link", { name: /open draft/i })).not.toBeInTheDocument();
  });

  it("does not blame the templates when the conflict is the research depth", async () => {
    /* 409 has two unrelated sources on this route now. Reading the depth refusal off the
       status would tell someone their post records no templates because they asked for less
       research than the new subject needs, and send them to fix a thing that is not wrong.
       `belowFloor` discriminates on the shape of `detail`, never on the status. */
    stubFetch(
      jsonResponse(409, {
        detail: {
          error: "this brief needs at least 'light' research (number, organisation)",
          requested_mode: "none",
          recommended_mode: "light",
          mode_signals: ["number", "organisation"],
        },
      }),
    );
    render(<RetopicForm postId={95} />);

    type("What changed in Acme checkout?");
    fireEvent.click(startButton());

    await waitFor(() => expect(toastError).toHaveBeenCalled());
    const [headline, options] = toastError.mock.calls[0];
    expect(headline).toMatch(/at least light research/i);
    expect(headline).not.toMatch(/templates/i);
    // The floor belongs to the new subject. Saying otherwise would suggest the source post
    // decides it, which is the inheritance `generation.retopic` deliberately refuses.
    expect(options.description).toMatch(/detected from the new subject/);
  });

  it("says the post itself is gone only on a 404", async () => {
    stubFetch(jsonResponse(404, { detail: "no post 95" }));
    render(<RetopicForm postId={95} />);

    type("why keyword-dense titles stall on Walmart");
    fireEvent.click(startButton());

    await waitFor(() => expect(toastError).toHaveBeenCalled());
    expect(toastError.mock.calls[0][0]).toMatch(/post/i);
    expect(toastError.mock.calls[0][0]).not.toMatch(/templates/i);
    expect(toastError.mock.calls[0][1]).toEqual({ description: "no post 95" });
  });

  it("says the backend is unreachable rather than claiming a draft was written", async () => {
    stubFetch(new TypeError("fetch failed"));
    render(<RetopicForm postId={95} />);

    type("why keyword-dense titles stall on Walmart");
    fireEvent.click(startButton());

    await waitFor(() => expect(toastError).toHaveBeenCalled());
    expect(toastError.mock.calls[0][1]).toEqual({ description: "fetch failed" });
    expect(toastSuccess).not.toHaveBeenCalled();
    expect(screen.queryByRole("link", { name: /open draft/i })).not.toBeInTheDocument();
  });

  it("re-enables itself after a failure so the subject can be sent again", async () => {
    stubFetch(jsonResponse(502, { detail: "the model answered with something unparseable" }));
    render(<RetopicForm postId={95} />);

    type("why keyword-dense titles stall on Walmart");
    fireEvent.click(startButton());

    await waitFor(() => expect(toastError).toHaveBeenCalled());
    expect(startButton()).toBeEnabled();
    expect(ideaBox()).toBeEnabled();
  });
});
