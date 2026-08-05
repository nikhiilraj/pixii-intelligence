import { render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import StudioPage from "./page";

/* What the page resolves before the panel ever sees it: which research run to show, and the
 * three ways there might not be one.
 *
 * Its own file, and `Studio` replaced with a spy, for the reason `page.narrowing.test.tsx`
 * gives — the thing under test is the object handed across the boundary, and every one of
 * these failures renders identically to a success. A `?research=` that quietly becomes a
 * request for `/research/` looks like a run that does not exist; a failed index read handed
 * down as `[]` looks like "no research has ever been run", which is the sentence that gets an
 * uncited draft pushed. */
const props = vi.hoisted(() => vi.fn());
vi.mock("./Studio", () => ({
  default: (p: Record<string, unknown>) => {
    props(p);
    return null;
  },
}));

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const DOSSIER = {
  job_id: 4,
  question: "What did the Act change?",
  mode: "light",
  recommended_mode: "light",
  mode_signals: [],
  state: "completed",
  researched_at: "2026-08-05T09:30:00Z",
  freshness_days: null,
  sources: [],
  claims: [],
  citations: [],
  unknowns: [],
  contradictions: [],
  spend: {
    queries: null,
    sources_found: null,
    sources_fetched: null,
    llm_calls: null,
    budget_exhausted: null,
  },
};

/** Every request the page makes, answered, with `research` overridable per test.
 *  Returns the spy so a test can assert on which paths were asked for. */
function stubFetch(answers: Record<string, Response> = {}) {
  const spy = vi.fn((url: string) => {
    const path = String(url).replace(/^https?:\/\/[^/]+/, "");
    if (answers[path]) return Promise.resolve(answers[path].clone());
    if (path === "/health")
      return Promise.resolve(
        jsonResponse(200, { status: "ok", database: true, credentials: {}, variants_max: 7 }),
      );
    if (path === "/publishing")
      return Promise.resolve(
        jsonResponse(200, { enabled: false, platform: "linkedin", account_id: null }),
      );
    return Promise.resolve(jsonResponse(200, []));
  });
  vi.stubGlobal("fetch", spy);
  return spy;
}

function paths(spy: ReturnType<typeof stubFetch>): string[] {
  return spy.mock.calls.map(([url]) => String(url).replace(/^https?:\/\/[^/]+/, ""));
}

describe("which research run the page resolves", () => {
  it("reads the dossier `?research=` names and hands it down verbatim", async () => {
    stubFetch({ "/research/4": jsonResponse(200, DOSSIER) });

    render(await StudioPage({ searchParams: Promise.resolve({ draft: "555", research: "4" }) }));

    const research = props.mock.calls.at(-1)?.[0].research;
    expect(research.dossier).toEqual(DOSSIER);
    expect(research.unavailable).toBeNull();

    vi.unstubAllGlobals();
  });

  it("does not request a dossier for a `?research=` that is not a number", async () => {
    /* The bug this closes is the one `?draft=` already closed: `?research=` empty reads as a
     * request for `/research/`, and `?research=1&research=2` arrives as an array. Both become
     * a request nobody meant to make, and both would come back as an error the panel would
     * then report as though a real run had failed. */
    const spy = stubFetch();

    render(await StudioPage({ searchParams: Promise.resolve({ draft: "555", research: "" }) }));

    expect(paths(spy).filter((p) => p.startsWith("/research/"))).toEqual([]);
    expect(props.mock.calls.at(-1)?.[0].research.unavailable).toBeNull();

    vi.unstubAllGlobals();
  });

  it("says which id was not a job id rather than requesting it", async () => {
    const spy = stubFetch();

    render(await StudioPage({ searchParams: Promise.resolve({ draft: "555", research: "abc" }) }));

    expect(paths(spy).filter((p) => p.startsWith("/research/"))).toEqual([]);
    expect(props.mock.calls.at(-1)?.[0].research.unavailable).toContain('"abc" is not a research');

    vi.unstubAllGlobals();
  });

  it("takes the first of a repeated `?research=` rather than requesting an array", async () => {
    const spy = stubFetch({ "/research/4": jsonResponse(200, DOSSIER) });

    render(
      await StudioPage({
        searchParams: Promise.resolve({ draft: "555", research: ["4", "9"] }),
      }),
    );

    expect(paths(spy)).toContain("/research/4");
    expect(paths(spy)).not.toContain("/research/9");

    vi.unstubAllGlobals();
  });

  it("reports a 404 as a failed read and hands down no dossier", async () => {
    stubFetch({ "/research/9": jsonResponse(404, { detail: "no research job 9" }) });

    render(await StudioPage({ searchParams: Promise.resolve({ draft: "555", research: "9" }) }));

    const research = props.mock.calls.at(-1)?.[0].research;
    expect(research.dossier).toBeNull();
    expect(research.unavailable).toBe("HTTP 404: no research job 9");

    vi.unstubAllGlobals();
  });

  it("hands down a null index when that read failed, never an empty list", async () => {
    /* A substituted default, not a crash — the failure `page.narrowing.test.tsx` catches for
     * the publishing target, in the place it does the most damage. `[]` here draws "No research
     * has been run", stated confidently, on a request that never arrived. */
    stubFetch({ "/research": jsonResponse(500, { detail: "boom" }) });

    render(await StudioPage({ searchParams: Promise.resolve({ draft: "555" }) }));

    expect(props.mock.calls.at(-1)?.[0].research.jobs).toBeNull();

    vi.unstubAllGlobals();
  });

  it("reads the index even when no run was named", async () => {
    // It is what a reviewer looking at an unresearched draft needs in order to reach one.
    const spy = stubFetch();

    render(await StudioPage({ searchParams: Promise.resolve({ draft: "555" }) }));

    expect(paths(spy)).toContain("/research");

    vi.unstubAllGlobals();
  });
});
