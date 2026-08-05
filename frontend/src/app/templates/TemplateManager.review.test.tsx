import "@testing-library/jest-dom/vitest";

import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Template } from "@/lib/api";

import TemplateManager from "./TemplateManager";

/* `defaultMode="review"` is what every visitor to `/templates` actually sees — `page.tsx`
 * passes it unconditionally. It is the human-approval gate the whole application is built
 * around: one proposal at a time, Approve / Retire / Decide-later, keyboard shortcuts. None
 * of that is in `TemplateManager.test.tsx`, which never passes `defaultMode` and so only ever
 * exercises `mode === "list"`. This file is the smoke test for the screen everyone lands on.
 *
 * Same stubbing discipline as the base file: every request is stubbed, and the stub rejects
 * any path this screen is not supposed to reach — `/templates/{id}/preview` deliberately
 * absent, so all fixtures below are `kind: "hook"` and never grow a Preview button to click.
 *
 * `router.refresh()` is mocked as a bare `vi.fn()`. It records that the call happened but
 * does not change `initial` the way a real refresh (a server re-read) would. A few tests call
 * RTL's `rerender` with an updated `initial` afterward to stand in for that re-read — named as
 * a simulation at each call site, since it proves what this component does once new props
 * arrive, not what a real Next.js refresh returns. */

const toastError = vi.hoisted(() => vi.fn());
const refresh = vi.hoisted(() => vi.fn());
vi.mock("sonner", () => ({ toast: { error: toastError, success: vi.fn() } }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh }) }));

function template(overrides: Partial<Template> = {}): Template {
  return {
    id: 3,
    family_id: "fam-hook",
    version: 1,
    kind: "hook",
    name: "transformation",
    status: "proposed",
    body: { pattern: "{value} turned into {outcome}", tone: "direct" },
    slots: [],
    provenance: [],
    notes: "",
    ...overrides,
  };
}

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

/** Only the two mutation endpoints this screen can reach. */
const ALLOWED = [/^\/templates\/\d+\/(approve|retire)$/];

function stubApi(response: Response = jsonResponse(200, { ok: true })) {
  const fetchStub = vi.fn((url: string) => {
    const path = String(url).replace(/^https?:\/\/[^/]+/, "");
    if (!ALLOWED.some((allowed) => allowed.test(path)))
      return Promise.reject(new Error(`unexpected request: ${path}`));
    return Promise.resolve(response.clone());
  });
  vi.stubGlobal("fetch", fetchStub);
  return fetchStub;
}

function requests(fetchStub: ReturnType<typeof vi.fn>) {
  return fetchStub.mock.calls.map(([url, init]) => {
    const { method, body } = (init ?? {}) as RequestInit;
    return {
      path: String(url).replace(/^https?:\/\/[^/]+/, ""),
      method,
      body: body === undefined ? undefined : JSON.parse(String(body)),
    };
  });
}

/** Three proposals in a fixed, named order — every test below reasons about "next in the
 *  queue" against this order. */
const A = template({ id: 1, name: "alpha" });
const B = template({ id: 2, name: "bravo" });
const C = template({ id: 3, name: "charlie" });

/** `send` fires `router.refresh()` before `setSession`/`setCursor` run, so waiting on the
 *  mocked `refresh` can win the race and let an assertion read state from before those two
 *  land. The session line is written by the same synchronous call that sets `cursor` — waiting
 *  for it to settle means the cursor update has too. */
async function waitForSession(text: RegExp) {
  await waitFor(() => expect(screen.getByText(text)).toBeInTheDocument());
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  toastError.mockReset();
  refresh.mockReset();
});

describe("review mode vs list mode", () => {
  it("shows the queue, not the authoring list, when defaultMode is review", () => {
    render(<TemplateManager initial={[A, B, C]} defaultMode="review" />);

    expect(screen.getByText("Use J / K to move")).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 2 })).toHaveTextContent("alpha");
    expect(screen.queryByRole("heading", { name: "Author a template" })).toBeNull();
  });

  it("shows the authoring list, not the queue, without defaultMode", () => {
    render(<TemplateManager initial={[A, B, C]} />);

    expect(screen.getByRole("heading", { name: "Author a template" })).toBeInTheDocument();
    expect(screen.queryByText("Use J / K to move")).toBeNull();
  });
});

describe("approve and retire", () => {
  it("approves the template on screen, at its own url", async () => {
    const fetchStub = stubApi();
    render(<TemplateManager initial={[A, B, C]} defaultMode="review" />);

    fireEvent.click(screen.getByRole("button", { name: /^Approve/ }));

    await waitForSession(/1 approved, 0 retired/);
    expect(requests(fetchStub)).toEqual([
      { path: "/templates/1/approve", method: "POST", body: undefined },
    ]);
    expect(refresh).toHaveBeenCalledTimes(1);
  });

  it("retires the template on screen, at its own url", async () => {
    const fetchStub = stubApi();
    render(<TemplateManager initial={[A, B, C]} defaultMode="review" />);

    fireEvent.click(screen.getByRole("button", { name: /^Retire/ }));

    await waitForSession(/0 approved, 1 retired/);
    expect(requests(fetchStub)).toEqual([
      { path: "/templates/1/retire", method: "POST", body: undefined },
    ]);
  });
});

describe("the cursor after a decision lands", () => {
  it("advances to what was next in the queue once the refresh lands", async () => {
    // The case the reviewer asked about: approve the middle item of three. "j" moves onto it.
    stubApi();
    const { rerender } = render(<TemplateManager initial={[A, B, C]} defaultMode="review" />);
    fireEvent.keyDown(document.body, { key: "j" });
    expect(screen.getByRole("heading", { level: 2 })).toHaveTextContent("bravo");

    fireEvent.click(screen.getByRole("button", { name: /^Approve/ }));
    await waitForSession(/1 approved, 0 retired/);

    // Before the refresh lands, bravo is still on screen — `initial` has not changed yet, only
    // local state has. In production this is a brief flash; here it holds until simulated.
    expect(screen.getByRole("heading", { level: 2 })).toHaveTextContent("bravo");

    // Simulates the server re-read a real `router.refresh()` would trigger: bravo comes back
    // approved and drops out of the proposed queue.
    rerender(
      <TemplateManager initial={[A, { ...B, status: "approved" }, C]} defaultMode="review" />,
    );

    // charlie was next after bravo in the original order, and that is where the queue lands —
    // the `setCursor` clamp in `decide` (`min(at, max(queue.length - 2, 0))`) and the display
    // clamp in `current`'s derivation both resolve to the same index here, because the refresh
    // removed exactly one item. They are redundant in this case, not contradictory.
    expect(screen.getByRole("heading", { level: 2 })).toHaveTextContent("charlie");
  });
});

describe("deciding later", () => {
  it("defers the item on screen and reorders the queue behind the rest", () => {
    render(<TemplateManager initial={[A, B, C]} defaultMode="review" />);
    expect(screen.getByRole("heading", { level: 2 })).toHaveTextContent("alpha");

    fireEvent.click(screen.getByRole("button", { name: /^Decide later/ }));

    // alpha moved to the back; bravo, next in the original order, is now on screen.
    expect(screen.getByRole("heading", { level: 2 })).toHaveTextContent("bravo");

    // "Next in the queue" lists charlie then alpha — alpha deferred, not dropped.
    const upcoming = screen.getByText("Next in the queue").nextElementSibling as HTMLElement;
    const names = within(upcoming)
      .getAllByText(/^(alpha|bravo|charlie)$/)
      .map((el) => el.textContent);
    expect(names).toEqual(["charlie", "alpha"]);
  });
});

describe("the empty queue state", () => {
  it("shows once every proposal is approved or retired", () => {
    render(
      <TemplateManager
        initial={[{ ...A, status: "approved" }, { ...B, status: "retired" }]}
        defaultMode="review"
      />,
    );

    expect(screen.getByText("The review queue is clear.")).toBeInTheDocument();
  });

  it("is not reached by deferring every proposal instead", () => {
    // Deferred items are appended to the queue, never dropped from it, so deferring all three
    // leaves `queue.length` at 3 and `decideLater`'s `setCursor(0)` lands back on the first one
    // — even though the empty state's own copy says "approved, retired or deferred."
    render(<TemplateManager initial={[A, B, C]} defaultMode="review" />);

    fireEvent.click(screen.getByRole("button", { name: /^Decide later/ })); // defers alpha
    fireEvent.click(screen.getByRole("button", { name: /^Decide later/ })); // defers bravo
    fireEvent.click(screen.getByRole("button", { name: /^Decide later/ })); // defers charlie

    expect(screen.getByRole("heading", { level: 2 })).toHaveTextContent("alpha");
    expect(screen.queryByText("The review queue is clear.")).toBeNull();
  });
});

describe("keyboard shortcuts", () => {
  it("moves the cursor with j and k", () => {
    render(<TemplateManager initial={[A, B, C]} defaultMode="review" />);
    expect(screen.getByRole("heading", { level: 2 })).toHaveTextContent("alpha");

    fireEvent.keyDown(document.body, { key: "j" });
    expect(screen.getByRole("heading", { level: 2 })).toHaveTextContent("bravo");

    fireEvent.keyDown(document.body, { key: "k" });
    expect(screen.getByRole("heading", { level: 2 })).toHaveTextContent("alpha");
  });

  it("approves the item on screen with a", async () => {
    const fetchStub = stubApi();
    render(<TemplateManager initial={[A, B, C]} defaultMode="review" />);

    fireEvent.keyDown(document.body, { key: "a" });

    await waitForSession(/1 approved, 0 retired/);
    expect(requests(fetchStub)).toEqual([
      { path: "/templates/1/approve", method: "POST", body: undefined },
    ]);
  });
});

describe("the session progress card", () => {
  it("reads its denominator off the live queue, which shrinks as items clear", async () => {
    // `cleared` only grows across the session; `proposals.length` is `initial.filter(status
    // === "proposed")`, which shrinks each time a refresh lands with one fewer proposal. Two
    // approvals into a session of three, the card's denominator has already fallen behind.
    stubApi();
    const { rerender } = render(<TemplateManager initial={[A, B, C]} defaultMode="review" />);

    fireEvent.click(screen.getByRole("button", { name: /^Approve/ })); // alpha
    await waitForSession(/1 approved, 0 retired/);
    rerender(
      <TemplateManager initial={[{ ...A, status: "approved" }, B, C]} defaultMode="review" />,
    );

    fireEvent.click(screen.getByRole("button", { name: /^Approve/ })); // bravo, now on screen
    await waitForSession(/2 approved, 0 retired/);
    rerender(
      <TemplateManager
        initial={[{ ...A, status: "approved" }, { ...B, status: "approved" }, C]}
        defaultMode="review"
      />,
    );

    // Scoped to "of N this session": the top bar carries its own "{cleared} cleared this
    // session" counter that also ends in "this session" and would otherwise match too.
    const denominator = screen.getByText(/^of \d+ this session$/);
    const cleared = denominator.previousElementSibling!;
    // Two cleared, one proposal left standing: the card reads "2 of 1 this session" rather
    // than counting against the 3 the session actually started with.
    expect(cleared).toHaveTextContent("2");
    expect(denominator).toHaveTextContent("of 1 this session");
  });
});
