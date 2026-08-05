import "@testing-library/jest-dom/vitest";

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import OperationsPage from "./page";

/* What the page itself decides, as opposed to what its panels decide: which reads it makes,
 * what it does with a `/health` that failed, and what a `?job=` that is not a number means.
 */

vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn() } }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh: vi.fn(), push: vi.fn() }) }));

const HEALTH = {
  status: "ok",
  database: true,
  credentials: {
    zernio: true,
    azure_chat: true,
    azure_image: true,
    cloudflare_rendering: true,
    firecrawl_search: true,
    brave_search: false,
  },
  variants_max: 3,
  autonomous_max_drafts: 2,
};

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

/** Answer each route separately, so a test can fail one read and leave the others working —
 *  which is the state the page has to distinguish. */
function route(handlers: Record<string, () => Response>) {
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string) => {
      const path = String(url).replace("http://localhost:8000", "");
      // Longest prefix wins, so `/research/4` is not swallowed by `/research` — which it
      // would be on insertion order, and silently: the dossier read would answer with the
      // list's body and the panel would render against the wrong shape.
      const key = Object.keys(handlers)
        .filter((prefix) => path.startsWith(prefix))
        .sort((a, b) => b.length - a.length)[0];
      return Promise.resolve(key ? handlers[key]() : jsonResponse(404, { detail: `no ${path}` }));
    }),
  );
}

const OK = {
  "/health": () => jsonResponse(200, HEALTH),
  "/research": () => jsonResponse(200, []),
  // Answered even where a test is about something else. Left out, this read falls through to
  // the 404 above and every one of these tests would quietly render a failed-read notice —
  // passing, while asserting nothing about a page that is reporting a broken read.
  "/daily-runs": () => jsonResponse(200, []),
};

async function open(params: Record<string, string> = {}) {
  return render(await OperationsPage({ searchParams: Promise.resolve(params) }));
}

afterEach(() => {
  vi.unstubAllGlobals();
  cleanup();
});

describe("the screen", () => {
  it("says what it will and will not do before any control is touched", async () => {
    route(OK);
    await open();

    expect(screen.getByRole("heading", { name: "Operations", level: 1 })).toBeInTheDocument();
    // The boundary the whole project is built on, stated on the one screen that generates
    // without a person watching each draft.
    expect(
      screen.getByText(/None of them publishes, schedules or pushes anything/),
    ).toBeInTheDocument();
  });

  it("has no control that could reach a publish or schedule command", async () => {
    route(OK);
    const { container } = await open();

    // ADR 0002: automation prepares and only an HTTP route a human hits may publish. This
    // screen adds four operations and none of them is one — asserted against the rendered
    // buttons rather than trusting the review that put them there.
    const labels = [...container.querySelectorAll("button")].map((b) => b.textContent ?? "");
    for (const label of labels) {
      expect(label.toLowerCase()).not.toMatch(/publish|schedule/);
    }
  });

  it("passes the credential flags through, so a panel can say what is missing", async () => {
    route({ ...OK, "/health": () => jsonResponse(200, { ...HEALTH, credentials: { ...HEALTH.credentials, zernio: false } }) });
    await open();

    expect(screen.getAllByText("Zernio not configured")).toHaveLength(2);
    expect(screen.getByRole("button", { name: "Pull from Zernio…" })).toBeDisabled();
  });

  it("passes the autonomous ceiling through rather than hardcoding it", async () => {
    route({ ...OK, "/health": () => jsonResponse(200, { ...HEALTH, autonomous_max_drafts: 7 }) });
    await open();

    // 7 is deliberately not the setting's default — a page hardcoding the number would print
    // 2 here and reproduce the drift `/health` exposes the field to remove.
    expect(screen.getByText(/The configured ceiling is 7/)).toBeInTheDocument();
  });
});

describe("the daily run log", () => {
  it("is read on load, so the page can say whether unattended work ran", async () => {
    route(OK);
    await open();

    const requested = (globalThis.fetch as unknown as { mock: { calls: unknown[][] } }).mock.calls.map(
      (call) => String(call[0]),
    );
    expect(requested.some((url) => url.includes("/daily-runs"))).toBe(true);
    // Reading the log must never be a way to start a run. Every request this page makes on
    // load is a GET; the one control that generates is a click behind a confirmation.
    const methods = (globalThis.fetch as unknown as { mock: { calls: unknown[][] } }).mock.calls.map(
      (call) => (call[1] as RequestInit | undefined)?.method ?? "GET",
    );
    expect(new Set(methods)).toEqual(new Set(["GET"]));
  });

  it("keeps its own failure, rather than taking the research list with it", async () => {
    route({
      ...OK,
      "/daily-runs": () => jsonResponse(500, { detail: "daily_run does not exist" }),
      "/research": () =>
        jsonResponse(200, [
          {
            job_id: 4,
            question: "how fast is adoption?",
            mode: "light",
            recommended_mode: "light",
            state: "completed",
            researched_at: "2026-08-04T10:05:00",
          },
        ]),
    });
    await open();

    // Four separate reads, four separate results. A single try/catch around the `Promise.all`
    // would have made one broken table blank the whole screen.
    expect(screen.getByText("daily_run does not exist")).toBeInTheDocument();
    expect(screen.getByText("how fast is adoption?")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run generation…" })).toBeEnabled();
  });
});

describe("a /health read that failed", () => {
  it("reports the failure and leaves every control enabled", async () => {
    route({ ...OK, "/health": () => jsonResponse(500, { detail: "the database went away" }) });
    await open();

    expect(screen.getByText("the database went away")).toBeInTheDocument();
    // Unknown is not off. Disabling on a request that did not arrive would hide capabilities
    // that are working — the `publishing === null` rule from PublishPanel.
    expect(screen.getByRole("button", { name: "Pull from Zernio…" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Sync metrics…" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Run generation…" })).toBeEnabled();
  });

  it("does not take the research list down with it", async () => {
    route({
      "/health": () => jsonResponse(500, { detail: "the database went away" }),
      "/daily-runs": () => jsonResponse(200, []),
      "/research": () =>
        jsonResponse(200, [
          {
            job_id: 4,
            question: "how fast is adoption?",
            mode: "light",
            recommended_mode: "light",
            state: "completed",
            researched_at: "2026-08-04T10:05:00",
          },
        ]),
    });
    await open();

    expect(screen.getByText("how fast is adoption?")).toBeInTheDocument();
  });
});

describe("the ?job= parameter", () => {
  it("opens the dossier it names", async () => {
    route({
      ...OK,
      "/research/4": () =>
        jsonResponse(200, {
          job_id: 4,
          question: "how fast is adoption?",
          mode: "light",
          recommended_mode: "light",
          mode_signals: [],
          state: "completed",
          researched_at: "2026-08-04T10:05:00",
          freshness_days: null,
          sources: [],
          claims: [],
          citations: [],
          unknowns: [],
          contradictions: [],
          spend: {
            queries: 0,
            sources_found: 0,
            sources_fetched: 0,
            llm_calls: 1,
            budget_exhausted: null,
          },
        }),
      "/research": () => jsonResponse(200, []),
      "/daily-runs": () => jsonResponse(200, []),
    });
    await open({ job: "4" });

    expect(screen.getByText("Claim coverage")).toBeInTheDocument();
  });

  it("says a non-numeric job id is not one, rather than silently showing nothing", async () => {
    route(OK);
    await open({ job: "nope" });

    // Without this the address reads as a selection and the page as an empty one — the same
    // rendering as not having asked at all.
    expect(screen.getByRole("alert")).toHaveTextContent('"nope" is not a research job id.');
    // And nothing was requested for it — the page never builds a URL out of what was typed.
    const requested = (globalThis.fetch as unknown as { mock: { calls: unknown[][] } }).mock.calls.map(
      (call) => String(call[0]),
    );
    expect(requested.some((url) => url.includes("/research/"))).toBe(false);
    expect(requested.some((url) => url.includes("/research"))).toBe(true);
  });
});
