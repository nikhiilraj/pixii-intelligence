import "@testing-library/jest-dom/vitest";

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Inbox, InboxItem } from "@/lib/api";

import InboxPage from "./page";

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
        : (routes.health ??
          /* `variants_max` is a sibling of `credentials`, not a key inside it: the footer
             renders `credentials` one health light per key, so a scalar in there would draw a
             bogus light. Carried in every /health *success* stub so these keep typechecking
             once `Health` gains the field. The 503 stub below is a failure body and does not
             get one — an error response carries `detail`, not a Health payload. */
          jsonResponse(200, {
            status: "ok",
            database: true,
            credentials: {},
            variants_max: 3,
          }));
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
    // The value today's database actually returns, so every test that does not care about the
    // counter still renders the state that ships.
    closed_circuits: 0,
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
    // Queues 2 and 3 hold drafts, and an `InboxItem.id` is the id of the thing its gate acts
    // on — so these are draft ids and each link opens that draft. Both read a bare `/studio`
    // until US-012, which was a link to a page that was empty on every single visit.
    expect(screen.getByRole("link", { name: /Why listings rot/ })).toHaveAttribute(
      "href",
      "/studio?draft=11",
    );
    expect(screen.getByRole("link", { name: /The 12\.7x spread/ })).toHaveAttribute(
      "href",
      "/studio?draft=12",
    );
    // Queue 4's item is a post, not a draft, so it is scoped to a different page entirely.
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

/* The one number on this page that is not a queue. It reads 0 against today's database and
   showing that 0 is the entire feature: a counter that hid itself at zero, or that rendered a
   dash, would leave "the loop has never run" and "we have no idea" looking identical — which is
   the state this page already had. */
/* The proposals queue held 50 items in one ungrouped column and the page stood 2998px tall, so
 * the gate that most often needs no action owned the fold. Bounded — but the count that is
 * hidden has to be *stated*, and it has to come from `queue.count` (the backend's own total,
 * the same number the heading prints) rather than from the length of the list after slicing,
 * which would always report the cap and never the truth. */
describe("a queue longer than the page shows", () => {
  const long = {
    count: 50,
    items: Array.from({ length: 50 }, (_, i) => item(i + 1, `Proposal ${i + 1}`, 50 - i)),
  };

  it("bounds the list and says how many it is holding back", async () => {
    stubFetch({
      inbox: jsonResponse(200, inbox({ proposals_awaiting_review: long })),
    });

    render(await InboxPage());

    // Six drawn, and they are the six the backend put first — oldest-waiting, not a re-sort.
    expect(screen.getAllByRole("link", { name: /^Proposal / })).toHaveLength(6);
    // By label text, not accessible name: the name concatenates the age Badge, so a `$`-anchored
    // name pattern can never match. `getByText` is exact, so "Proposal 1" excludes "Proposal 10".
    expect(screen.getByText("Proposal 1")).toBeInTheDocument();
    expect(screen.queryByText("Proposal 7")).not.toBeInTheDocument();

    // The total is the queue's own count, not the number of rows drawn.
    expect(screen.getByText(/Showing the 6 that have waited longest, of 50/)).toBeInTheDocument();
  });

  it("says nothing about a bound on a queue short enough to show whole", async () => {
    stubFetch({
      inbox: jsonResponse(
        200,
        inbox({
          proposals_awaiting_review: { count: 2, items: [item(1, "One", 1), item(2, "Two", 2)] },
        }),
      ),
    });

    render(await InboxPage());

    expect(screen.getAllByRole("link", { name: /One|Two/ })).toHaveLength(2);
    expect(screen.queryByText(/Showing the/)).not.toBeInTheDocument();
  });
});

describe("the circuit counter", () => {
  it("reads 0 as never-yet, not as an error and not as nothing to show", async () => {
    stubFetch({ inbox: jsonResponse(200, inbox()) });

    render(await InboxPage());

    expect(screen.getByRole("heading", { name: "Closed circuits: 0" })).toBeInTheDocument();
    // 0 is a fact about the circuit, stated in words, plus what would make it 1.
    expect(screen.getByText(/never been round/)).toBeInTheDocument();
    expect(screen.getByText(/makes this 1/)).toBeInTheDocument();
    // Not a failure and not a warning: nothing is broken, the lap simply has not happened.
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("states the real count once laps have closed, and drops the never-yet copy", async () => {
    stubFetch({ inbox: jsonResponse(200, inbox({ closed_circuits: 3 })) });

    render(await InboxPage());

    expect(screen.getByRole("heading", { name: "Closed circuits: 3" })).toBeInTheDocument();
    expect(screen.queryByText(/never been round/)).not.toBeInTheDocument();
  });

  it("says it is a lap count and not a score, at every value", async () => {
    // The page's own constraint: these are queues, not scores. A bare number in the header is
    // exactly what invites the comparison the rest of the page refuses, so the disclaimer is
    // part of the counter rather than a thing the reader is trusted to remember.
    for (const closed_circuits of [0, 3]) {
      stubFetch({ inbox: jsonResponse(200, inbox({ closed_circuits })) });
      render(await InboxPage());
      expect(screen.getByText(/not a score/)).toBeInTheDocument();
      cleanup();
    }
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
    // Same direction for the counter: a failed read has no lap count, and rendering "0" from
    // a response that never arrived would be the page asserting the strongest claim it makes
    // about the circuit on the strength of no data at all.
    expect(screen.queryByText(/Closed circuits/)).not.toBeInTheDocument();
  });

  it("says the backend is unreachable when it is, rather than showing four empty queues", async () => {
    stubFetch({ inbox: new TypeError("fetch failed") });

    render(await InboxPage());

    expect(screen.getByRole("status")).toHaveTextContent(/Backend unreachable/);
    expect(screen.queryByText(/Nothing is waiting on a verdict/)).not.toBeInTheDocument();
    // The retry sits beside the unreachable notice, not inside it: `make api` in the other
    // terminal is exactly what a reload would then pick up.
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });

  it("offers a retry beside the failure", async () => {
    stubFetch({ inbox: jsonResponse(500, { detail: "the database went away mid-query" }) });

    render(await InboxPage());

    // Every read on this page is a server-component fetch, so the retry is a reload and what a
    // test can honestly assert is that it is present and labelled. That a retry re-requests is
    // asserted where one is a real client call — `app/posts/Explorer.test.tsx`.
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
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
        variants_max: 3,
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

  it("says the API is down when the backend answered and called itself unhealthy", async () => {
    // The row that could only ever be green. `database` and every credential are true here on
    // purpose: the only "down" the page may contain is the API row's own, so the assertion
    // cannot pass by picking up somebody else's badge.
    stubFetch({
      inbox: jsonResponse(200, inbox()),
      health: jsonResponse(200, {
        status: "degraded",
        database: true,
        credentials: { zernio: true },
        variants_max: 3,
      }),
    });

    render(await InboxPage());

    expect(screen.getByText("API")).toHaveTextContent("down");
    expect(screen.getByText("Database")).toHaveTextContent("ok");
    // A backend that answered is not a backend that could not be reached. These two failures
    // are different states and must read differently — the transport-failure sentences belong
    // to the /health-never-arrived path, not to this one.
    expect(screen.queryByText(/Status unavailable/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Backend unreachable/)).not.toBeInTheDocument();
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
    // And no retry. A footer gets a footer-sized sentence: offering to reload the whole page
    // because the status strip failed would put the queues at risk to recover a strip.
    expect(screen.queryByRole("button", { name: "Try again" })).not.toBeInTheDocument();
  });
});
