import "@testing-library/jest-dom/vitest";

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import InboxPage from "@/app/page";
import type { Inbox, InboxItem } from "@/lib/api";

/* The Inbox is the page the error-vs-empty bug class costs the most: four queues whose entire
   job is to say "nothing is waiting", rendered from a request that can fail. A failed /inbox
   that renders four designed empty states is a page confidently reporting an empty circuit while
   the backend is on fire.
 *
 * Per the frontend testing policy: our own logic only. No Radix here to re-assert, and no claim
 * about contrast or layout — this is jsdom. */

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

/** The page fires two reads in parallel, so the stub dispatches on path rather than answering
 *  every call the same way — that is also what lets one of the two fail on its own. */
function stubFetch(routes: { inbox: Response | Error; health?: Response | Error }) {
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string) => {
      const answer = url.includes("/inbox")
        ? routes.inbox
        : (routes.health ?? jsonResponse(200, { status: "ok", database: true, credentials: {} }));
      return answer instanceof Error ? Promise.reject(answer) : Promise.resolve(answer);
    }),
  );
}

function item(id: number, label: string, ageDays: number): InboxItem {
  const since = new Date(Date.now() - ageDays * 86_400_000).toISOString();
  return { id, label, waiting_since: since, age_days: ageDays };
}

function inbox(overrides: Partial<Inbox> = {}): Inbox {
  const empty = { count: 0, items: [] };
  return {
    proposals_awaiting_review: empty,
    built_awaiting_push: empty,
    pushed_awaiting_monte: empty,
    published_awaiting_verdict: empty,
    ...overrides,
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
  // Explicit because `globals: false` — RTL registers its own cleanup only when the framework's
  // hooks are global. Without it the previous test's DOM is still mounted and every "this is
  // absent" assertion below passes on leftovers, which are the assertions that must be true.
  cleanup();
});

describe("the four queues", () => {
  it("renders every item in every queue, each linking to the page that clears it", async () => {
    stubFetch({
      inbox: jsonResponse(
        200,
        inbox({
          proposals_awaiting_review: { count: 1, items: [item(7, "Contrarian open", 3)] },
          built_awaiting_push: { count: 1, items: [item(11, "Why listings rot", 1)] },
          pushed_awaiting_monte: { count: 1, items: [item(12, "The 12.7x spread", 9)] },
          published_awaiting_verdict: { count: 2, items: [item(40, "A post that went live", 0), item(41, "Another", 2)] },
        }),
      ),
    });

    render(await InboxPage());

    expect(screen.getByRole("link", { name: /Contrarian open/ })).toHaveAttribute(
      "href",
      "/templates",
    );
    expect(screen.getByRole("link", { name: /Why listings rot/ })).toHaveAttribute(
      "href",
      "/studio",
    );
    expect(screen.getByRole("link", { name: /The 12\.7x spread/ })).toHaveAttribute(
      "href",
      "/studio",
    );
    // Queue 4 is the only one whose item has a page of its own, so its link is item-scoped.
    expect(screen.getByRole("link", { name: /A post that went live/ })).toHaveAttribute(
      "href",
      "/posts/40",
    );
    expect(screen.getByRole("link", { name: /Another/ })).toHaveAttribute("href", "/posts/41");
  });

  it("states an age on every item, and never '1 days' or '0 days'", async () => {
    stubFetch({
      inbox: jsonResponse(
        200,
        inbox({
          built_awaiting_push: {
            count: 3,
            items: [item(1, "Fresh", 0), item(2, "Yesterday", 1), item(3, "Stalled", 12)],
          },
        }),
      ),
    });

    render(await InboxPage());

    expect(screen.getByRole("link", { name: /Fresh/ })).toHaveTextContent("arrived today");
    expect(screen.getByRole("link", { name: /Yesterday/ })).toHaveTextContent("waiting 1 day");
    expect(screen.getByRole("link", { name: /Stalled/ })).toHaveTextContent("waiting 12 days");
    // The bug the wording exists to prevent, asserted rather than assumed.
    expect(screen.queryByText(/waiting 1 days/)).not.toBeInTheDocument();
    expect(screen.queryByText(/waiting 0 days/)).not.toBeInTheDocument();
  });

  it("shows each queue's designed empty state when it is genuinely empty", async () => {
    stubFetch({ inbox: jsonResponse(200, inbox()) });

    render(await InboxPage());

    // Four distinct sentences, each naming what would fill that queue — not one shared
    // "nothing here" and not an absence of anything at all.
    expect(screen.getByText(/No template is awaiting review/)).toBeInTheDocument();
    expect(screen.getByText(/No draft is waiting to be pushed/)).toBeInTheDocument();
    expect(screen.getByText(/No draft is waiting on a publish/)).toBeInTheDocument();
    expect(screen.getByText(/Nothing is waiting on a verdict/)).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("keeps a populated queue's items instead of its empty state", async () => {
    stubFetch({
      inbox: jsonResponse(
        200,
        inbox({
          proposals_awaiting_review: { count: 1, items: [item(7, "Contrarian open", 3)] },
        }),
      ),
    });

    render(await InboxPage());

    expect(screen.queryByText(/No template is awaiting review/)).not.toBeInTheDocument();
    // The other three are still empty, so their copy stays — an empty queue beside a full one.
    expect(screen.getByText(/Nothing is waiting on a verdict/)).toBeInTheDocument();
  });
});

/* The bug class this codebase keeps hitting, in both directions. One direction alone is not a
   test: "always render the error" passes the first assertion, "never render the error" passes
   the second. */
describe("a failed /inbox is not an empty inbox", () => {
  it("reports a 500 and withholds all four empty states", async () => {
    stubFetch({ inbox: jsonResponse(500, { detail: "the database went away mid-query" }) });

    render(await InboxPage());

    expect(screen.getByRole("alert")).toHaveTextContent("Request failed — HTTP 500");
    expect(screen.getByRole("alert")).toHaveTextContent("the database went away mid-query");
    expect(screen.queryByText(/No template is awaiting review/)).not.toBeInTheDocument();
    expect(screen.queryByText(/No draft is waiting to be pushed/)).not.toBeInTheDocument();
    expect(screen.queryByText(/No draft is waiting on a publish/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Nothing is waiting on a verdict/)).not.toBeInTheDocument();
  });

  it("says the backend is unreachable when it is, rather than showing four empty queues", async () => {
    stubFetch({ inbox: new TypeError("fetch failed") });

    render(await InboxPage());

    expect(screen.getByRole("status")).toHaveTextContent(/Backend unreachable/);
    expect(screen.queryByText(/Nothing is waiting on a verdict/)).not.toBeInTheDocument();
  });
});

describe("the health footer", () => {
  it("reports each credential and the database", async () => {
    stubFetch({
      inbox: jsonResponse(200, inbox()),
      health: jsonResponse(200, {
        status: "ok",
        database: true,
        credentials: { zernio: true, azure_openai: false },
      }),
    });

    render(await InboxPage());

    // Asserted on the row itself, NOT on `.parentElement`: the parent is the flex container
    // holding all four rows, so `toHaveTextContent("down")` there passes whenever *any* row is
    // down — the label would not be bound to its badge, and inverting `ok` would still pass.
    // A row's own textContent is "azure_openaidown", which is the binding worth pinning.
    expect(screen.getByText("API")).toHaveTextContent("ok");
    expect(screen.getByText("Database")).toHaveTextContent("ok");
    expect(screen.getByText("zernio")).toHaveTextContent("ok");
    expect(screen.getByText("azure_openai")).toHaveTextContent("down");
  });

  it("does not take the queues down with it when only /health fails", async () => {
    // The footer is a footer. A failed status read may not blank a page whose own request
    // succeeded — and it may not claim the backend is unreachable either, since it plainly
    // answered the read above.
    stubFetch({
      inbox: jsonResponse(
        200,
        inbox({ built_awaiting_push: { count: 1, items: [item(11, "Why listings rot", 4)] } }),
      ),
      health: jsonResponse(503, { detail: "database connection refused" }),
    });

    render(await InboxPage());

    expect(screen.getByRole("link", { name: /Why listings rot/ })).toBeInTheDocument();
    expect(screen.getByText(/Status unavailable/)).toHaveTextContent(
      "HTTP 503: database connection refused",
    );
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.queryByText(/Backend unreachable/)).not.toBeInTheDocument();
  });
});
