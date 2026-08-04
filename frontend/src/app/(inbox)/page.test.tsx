import "@testing-library/jest-dom/vitest";

import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Inbox, InboxItem } from "@/lib/api";

import InboxPage from "./page";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function stubFetch(routes: { inbox: Response | Error; health?: Response | Error }) {
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string) => {
      const answer = url.includes("/inbox")
        ? routes.inbox
        : (routes.health ??
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
  return {
    id,
    label,
    waiting_since: new Date(Date.now() - ageDays * 86_400_000).toISOString(),
    age_days: ageDays,
  };
}

function inbox(overrides: Partial<Inbox> = {}): Inbox {
  return {
    proposals_awaiting_review: { count: 0, items: [] },
    built_awaiting_push: { count: 0, items: [] },
    pushed_awaiting_monte: { count: 0, items: [] },
    published_awaiting_verdict: { count: 0, items: [] },
    closed_circuits: 0,
    ...overrides,
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
  cleanup();
});

describe("the daily worklist", () => {
  it("combines all four gates, orders by wait time, and keeps the right destinations", async () => {
    stubFetch({
      inbox: jsonResponse(
        200,
        inbox({
          proposals_awaiting_review: { count: 1, items: [item(7, "Contrarian open", 3)] },
          built_awaiting_push: { count: 1, items: [item(11, "Why listings rot", 1)] },
          pushed_awaiting_monte: { count: 1, items: [item(12, "The 12.7x spread", 9)] },
          published_awaiting_verdict: {
            count: 1,
            items: [item(40, "A post that went live", 2)],
          },
        }),
      ),
    });

    render(await InboxPage());

    const rows = within(screen.getByRole("list")).getAllByRole("listitem");
    expect(rows.map((row) => row.textContent)).toEqual([
      expect.stringContaining("The 12.7x spread"),
      expect.stringContaining("Contrarian open"),
      expect.stringContaining("A post that went live"),
      expect.stringContaining("Why listings rot"),
    ]);
    expect(screen.getByRole("link", { name: "Contrarian open" })).toHaveAttribute(
      "href",
      "/templates",
    );
    expect(screen.getByRole("link", { name: "Why listings rot" })).toHaveAttribute(
      "href",
      "/studio?draft=11",
    );
    expect(screen.getByRole("link", { name: "The 12.7x spread" })).toHaveAttribute(
      "href",
      "/studio?draft=12",
    );
    expect(screen.getByRole("link", { name: "A post that went live" })).toHaveAttribute(
      "href",
      "/posts/40",
    );
  });

  it("uses the largest queue to set the primary action", async () => {
    stubFetch({
      inbox: jsonResponse(
        200,
        inbox({
          proposals_awaiting_review: {
            count: 50,
            items: [item(1, "First proposal", 6)],
          },
          built_awaiting_push: { count: 4, items: [item(2, "One draft", 2)] },
        }),
      ),
    });

    render(await InboxPage());

    expect(screen.getByRole("heading", { name: "54 things are waiting on you" })).toBeVisible();
    expect(screen.getByRole("link", { name: "Review 50 proposals →" })).toHaveAttribute(
      "href",
      "/templates",
    );
  });

  it("bounds the home screen and states how much work is behind the first eight", async () => {
    const proposals = Array.from({ length: 50 }, (_, index) =>
      item(index + 1, `Proposal ${index + 1}`, 50 - index),
    );
    stubFetch({
      inbox: jsonResponse(
        200,
        inbox({ proposals_awaiting_review: { count: 50, items: proposals } }),
      ),
    });

    render(await InboxPage());

    expect(within(screen.getByRole("list")).getAllByRole("listitem")).toHaveLength(8);
    expect(screen.getByText("42 more waiting. Open the gate destination to work through the rest.")).toBeVisible();
    expect(screen.getByText("Proposal 1")).toBeVisible();
    expect(screen.queryByText("Proposal 9")).not.toBeInTheDocument();
  });

  it("spells the three useful age forms without inventing a zero-day wait", async () => {
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

    expect(screen.getAllByText("today").length).toBeGreaterThan(0);
    expect(screen.getAllByText("1 day").length).toBeGreaterThan(0);
    expect(screen.getByText("12 days")).toBeVisible();
    expect(screen.getByText(/oldest waiting 12 days/)).toBeVisible();
    expect(screen.queryByText(/waiting 1 days|waiting 0 days/)).not.toBeInTheDocument();
  });
});

describe("truthful empty and failure states", () => {
  it("treats a genuinely clear circuit as useful state, including the never-arrived gate", async () => {
    stubFetch({ inbox: jsonResponse(200, inbox()) });

    render(await InboxPage());

    expect(screen.getByText(/Nothing is waiting\. The circuit is clear/)).toBeVisible();
    expect(screen.getByText(/Nothing has ever arrived at gate 04/)).toBeVisible();
    expect(screen.getByText(/The circuit has never been round/)).toBeVisible();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("renders a failed Inbox read as an error rather than an empty circuit", async () => {
    stubFetch({ inbox: jsonResponse(503, { detail: "database unavailable" }) });

    render(await InboxPage());

    expect(screen.getByRole("alert")).toHaveTextContent("database unavailable");
    expect(screen.queryByText(/Nothing is waiting\. The circuit is clear/)).not.toBeInTheDocument();
    expect(screen.queryByText(/closed circuits/i)).not.toBeInTheDocument();
  });

  it("lets a health failure stay a footer-sized issue beside a populated Inbox", async () => {
    stubFetch({
      inbox: jsonResponse(
        200,
        inbox({ built_awaiting_push: { count: 1, items: [item(3, "Ready draft", 2)] } }),
      ),
      health: jsonResponse(503, { detail: "health timed out" }),
    });

    render(await InboxPage());

    expect(screen.getByText("Ready draft")).toBeVisible();
    expect(screen.getByText(/Status unavailable.*health timed out/)).toBeVisible();
  });
});

describe("the circuit counter", () => {
  it("shows a measured zero and explains that it means never-yet", async () => {
    stubFetch({ inbox: jsonResponse(200, inbox({ closed_circuits: 0 })) });

    render(await InboxPage());

    expect(screen.getByText(/the return path · 0 laps/)).toBeVisible();
    expect(screen.getByText(/The circuit has never been round/)).toBeVisible();
  });

  it("states the recorded count and removes the never-yet claim after a lap closes", async () => {
    stubFetch({ inbox: jsonResponse(200, inbox({ closed_circuits: 3 })) });

    render(await InboxPage());

    expect(screen.getByText("3 laps · the circuit has run end to end")).toBeVisible();
    expect(screen.queryByText(/The circuit has never been round/)).not.toBeInTheDocument();
  });
});
