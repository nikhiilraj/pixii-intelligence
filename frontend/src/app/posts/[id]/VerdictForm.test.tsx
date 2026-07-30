import "@testing-library/jest-dom/vitest";

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { VERDICT_NOTE_MAX, type Post } from "@/lib/api";

import VerdictForm from "./VerdictForm";

/* The verdict form is the only way human judgement enters this system, and the two ways it can
 * be wrong are both silent:
 *
 *   1. the request body key. `VerdictIn` (backend/app/main.py:268) names the field `note` and
 *      defaults it to "", so posting `verdict_note` succeeds and stores an empty reason — and an
 *      empty reason is skipped by `verdict_lessons` (generation.py:211). A ruling that teaches
 *      nothing, with a 200 in front of it.
 *   2. the note carried forward. The route overwrites `verdict_note` unconditionally, so a form
 *      whose box starts empty destroys a written reason on a worked -> mixed change.
 *
 * Both are asserted on the parsed body rather than on "fetch was called".
 *
 * Per the frontend testing policy: our own logic only. No Radix in this component to re-assert,
 * and no claim about contrast or layout — this is jsdom. */

const toastError = vi.hoisted(() => vi.fn());
const toastSuccess = vi.hoisted(() => vi.fn());
vi.mock("sonner", () => ({ toast: { error: toastError, success: toastSuccess } }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh: vi.fn() }) }));

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

/** The route answers with the whole row, and the form reads its display back off that row, so a
 *  stub that returned `{ok: true}` would not exercise the path the component actually takes. */
function postRow(overrides: Partial<Post> = {}): Partial<Post> {
  return { id: 7, verdict: null, verdict_note: "", verdict_at: null, ...overrides };
}

/** Typed with fetch's own signature, not inferred from the zero-argument implementation — that
 *  is what makes `spy.mock.calls[0][1]` readable, and reading it is the point here. */
function stubFetch(answer: Response | Error) {
  const spy = vi.fn<(url: string, init?: RequestInit) => Promise<Response>>(() =>
    answer instanceof Error ? Promise.reject(answer) : Promise.resolve(answer),
  );
  vi.stubGlobal("fetch", spy);
  return spy;
}

/** What actually went over the wire, parsed — the assertion that catches a renamed field. */
function sentBody(spy: ReturnType<typeof stubFetch>): unknown {
  return JSON.parse(String(spy.mock.calls[0]?.[1]?.body));
}

function pick(label: string) {
  fireEvent.click(screen.getByRole("button", { name: label }));
}

function submit(name: RegExp = /ruling$/) {
  fireEvent.click(screen.getByRole("button", { name }));
}

function noteBox(): HTMLTextAreaElement {
  return screen.getByLabelText(/^Why/) as HTMLTextAreaElement;
}

afterEach(() => {
  vi.unstubAllGlobals();
  toastError.mockClear();
  toastSuccess.mockClear();
  // Explicit because `globals: false` — RTL only registers its own cleanup when the framework's
  // hooks are global. Without it the previous test's DOM stays mounted and every "this did not
  // change" assertion below passes on leftovers, which are exactly the assertions that matter.
  cleanup();
});

describe("recording a verdict", () => {
  it("sends the verdict and the note under the field names the route declares", async () => {
    const spy = stubFetch(
      jsonResponse(200, postRow({ verdict: "worked", verdict_note: "the opening number did the work" })),
    );
    render(<VerdictForm postId={7} verdict={null} note="" />);

    pick("Worked");
    fireEvent.change(noteBox(), { target: { value: "the opening number did the work" } });
    submit();

    await waitFor(() => expect(spy).toHaveBeenCalled());
    expect(spy.mock.calls[0][0]).toContain("/posts/7/verdict");
    expect(spy.mock.calls[0][1]?.method).toBe("POST");
    // Deep-equal, not `toMatchObject`: `note` is the key that can be wrong without failing.
    expect(sentBody(spy)).toEqual({ verdict: "worked", note: "the opening number did the work" });
  });

  it("offers no submit until a verdict has been picked", () => {
    stubFetch(jsonResponse(200, postRow()));
    render(<VerdictForm postId={7} verdict={null} note="" />);

    // A note without a ruling is not a verdict, and the route has no way to express one.
    fireEvent.change(noteBox(), { target: { value: "a reason with nothing attached" } });
    expect(screen.getByRole("button", { name: "Record the ruling" })).toBeDisabled();

    pick("Mixed");
    expect(screen.getByRole("button", { name: "Record the ruling" })).toBeEnabled();
  });

  it("takes the saved ruling from the row the route returned", async () => {
    const spy = stubFetch(jsonResponse(200, postRow({ verdict: "mixed", verdict_note: "worth a second go" })));
    render(<VerdictForm postId={7} verdict={null} note="" />);

    pick("Mixed");
    submit();

    // `router.refresh()` is a no-op in jsdom, so a display driven only by props would still read
    // "Record the ruling" here — and would in a real browser too until the server answered.
    await waitFor(() => expect(screen.getByText("Recorded: Mixed")).toBeInTheDocument());
    expect(screen.getByRole("button", { name: "Change the ruling" })).toBeInTheDocument();
    expect(noteBox()).toHaveValue("worth a second go");
    expect(toastSuccess).toHaveBeenCalledWith("Ruling saved");
    expect(sentBody(spy)).toEqual({ verdict: "mixed", note: "" });
  });
});

describe("an existing verdict", () => {
  it("is shown, with its note, as the state of the form", () => {
    stubFetch(jsonResponse(200, postRow()));
    render(<VerdictForm postId={7} verdict="worked" note="the opening number did the work" />);

    expect(screen.getByText("Recorded: Worked")).toBeInTheDocument();
    expect(screen.getByRole("button", { pressed: true })).toHaveTextContent("Worked");
    expect(noteBox()).toHaveValue("the opening number did the work");
    // The label says the ruling exists and this would replace it, not that none has been made.
    expect(screen.getByRole("button", { name: "Change the ruling" })).toBeInTheDocument();
  });

  it("can be changed, and the written reason is carried over rather than wiped", async () => {
    const spy = stubFetch(
      jsonResponse(200, postRow({ verdict: "mixed", verdict_note: "the opening number did the work" })),
    );
    render(<VerdictForm postId={7} verdict="worked" note="the opening number did the work" />);

    pick("Mixed");
    expect(screen.getByRole("button", { pressed: true })).toHaveTextContent("Mixed");
    submit();

    // The route assigns `verdict_note = payload.note` with no conditional, so sending "" here
    // would silently delete the human's reasoning on a verdict change.
    await waitFor(() => expect(spy).toHaveBeenCalled());
    expect(sentBody(spy)).toEqual({
      verdict: "mixed",
      note: "the opening number did the work",
    });
    await waitFor(() => expect(screen.getByText("Recorded: Mixed")).toBeInTheDocument());
  });
});

describe("the 500-character cap", () => {
  it("states the count against the cap while the note is being written", () => {
    stubFetch(jsonResponse(200, postRow()));
    render(<VerdictForm postId={7} verdict={null} note="" />);

    expect(screen.getByText(`0 of ${VERDICT_NOTE_MAX} characters`)).toBeInTheDocument();

    fireEvent.change(noteBox(), { target: { value: "a".repeat(120) } });
    expect(screen.getByText(`120 of ${VERDICT_NOTE_MAX} characters`)).toBeInTheDocument();

    fireEvent.change(noteBox(), { target: { value: "a".repeat(VERDICT_NOTE_MAX) } });
    expect(screen.getByText(new RegExp(`${VERDICT_NOTE_MAX} characters — at the cap`))).toBeInTheDocument();
  });

  it("caps the box at the number the route accepts, so the 422 is unreachable", () => {
    stubFetch(jsonResponse(200, postRow()));
    render(<VerdictForm postId={7} verdict={null} note="" />);

    // `fireEvent.change` writes straight to the value and bypasses maxLength, so the attribute
    // is what a jsdom test can honestly assert; enforcing it is the browser's job and is not
    // asserted here. The cap is read from the exported constant, so a change on the route
    // breaks this and not only the backend's own boundary test.
    expect(noteBox()).toHaveAttribute("maxlength", String(VERDICT_NOTE_MAX));
    expect(VERDICT_NOTE_MAX).toBe(500);
  });
});

/* The bug class this codebase keeps hitting, on the surface where it costs the most: a form that
   reports a ruling as saved when the API refused it leaves a human believing their judgement is
   recorded. Asserted in both directions — the failure is surfaced AND success is withheld. */
describe("a refused submit", () => {
  it("surfaces the API's own detail and does not render as success", async () => {
    stubFetch(
      jsonResponse(422, { detail: `verdict note is 640 characters; the cap is ${VERDICT_NOTE_MAX}` }),
    );
    render(<VerdictForm postId={7} verdict="worked" note="the opening number did the work" />);

    pick("Mixed");
    submit();

    await waitFor(() =>
      expect(toastError).toHaveBeenCalledWith("Could not save your ruling", {
        description: `verdict note is 640 characters; the cap is ${VERDICT_NOTE_MAX}`,
      }),
    );
    expect(toastSuccess).not.toHaveBeenCalled();
    // The recorded ruling is still the one the database holds, not the one that was clicked.
    expect(screen.getByText("Recorded: Worked")).toBeInTheDocument();
    expect(screen.queryByText("Recorded: Mixed")).not.toBeInTheDocument();
    expect(noteBox()).toHaveValue("the opening number did the work");
  });

  it("says the backend is unreachable rather than claiming the ruling landed", async () => {
    stubFetch(new TypeError("fetch failed"));
    render(<VerdictForm postId={7} verdict={null} note="" />);

    pick("Worked");
    submit();

    await waitFor(() =>
      expect(toastError).toHaveBeenCalledWith("Could not save your ruling", {
        description: "fetch failed",
      }),
    );
    expect(toastSuccess).not.toHaveBeenCalled();
    expect(screen.queryByText(/^Recorded:/)).not.toBeInTheDocument();
    // Still the pre-submit label: nothing was recorded, so nothing is offered as a change.
    expect(screen.getByRole("button", { name: "Record the ruling" })).toBeInTheDocument();
  });

  it("re-enables the form after a failure so the ruling can be sent again", async () => {
    stubFetch(jsonResponse(500, { detail: "the database went away mid-query" }));
    render(<VerdictForm postId={7} verdict={null} note="" />);

    pick("Worked");
    submit();

    await waitFor(() => expect(toastError).toHaveBeenCalled());
    expect(screen.getByRole("button", { name: "Record the ruling" })).toBeEnabled();
    expect(noteBox()).toBeEnabled();
  });
});

// A verdict is judgement at n=1. `test_generation.py:532` pins that vocabulary out of the prompt
// path; this holds the same line on the words a human actually reads. The list is that test's,
// verbatim, and the match is a lowercased substring — "performance" is caught by "perform".
describe("the copy makes no claim about ranking", () => {
  it("uses none of the vocabulary the prompt path bans", () => {
    stubFetch(jsonResponse(200, postRow()));
    const { container } = render(
      <VerdictForm postId={7} verdict="mixed" note="worth a second go" />,
    );

    const words = (container.textContent ?? "").toLowerCase();
    expect(words).not.toBe("");
    for (const banned of ["best", "rank", "perform", "score", "top-", "average", "win rate"]) {
      expect(words).not.toContain(banned);
    }
  });
});
