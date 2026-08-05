import { render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import StudioPage from "./page";

const props = vi.hoisted(() => vi.fn());
vi.mock("./Studio", () => ({
  default: (value: Record<string, unknown>) => {
    props(value);
    return null;
  },
}));

function response(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

function stubFetch(draft: Record<string, unknown>) {
  const fetcher = vi.fn((url: string) => {
    const path = String(url).replace(/^https?:\/\/[^/]+/, "");
    if (path === "/templates" || path === "/assets" || path === "/drafts") {
      return Promise.resolve(response([]));
    }
    if (path === "/health") return Promise.resolve(response({ variants_max: 3 }));
    if (path === "/publishing") {
      return Promise.resolve(response({ enabled: false, platform: "linkedin", account_id: null }));
    }
    if (path === "/drafts/7") return Promise.resolve(response(draft));
    if (path === "/drafts/7/publications") return Promise.resolve(response([]));
    return Promise.reject(new Error(`unexpected request: ${path}`));
  });
  vi.stubGlobal("fetch", fetcher);
  return fetcher;
}

afterEach(() => {
  props.mockReset();
  vi.unstubAllGlobals();
});

describe("persisted draft-to-research lineage", () => {
  it("does not resolve the removed ?research= workaround", async () => {
    const draft = { id: 7, idea: "x", editorial: { research_job_id: 4, research: null } };
    const fetcher = stubFetch(draft);

    render(
      await StudioPage({
        searchParams: Promise.resolve({ draft: "7", research: "999" }),
      }),
    );

    const paths = fetcher.mock.calls.map(([url]) => String(url).replace(/^https?:\/\/[^/]+/, ""));
    expect(paths.some((path) => path.startsWith("/research"))).toBe(false);
    expect(props.mock.calls.at(-1)?.[0].initialDraft).toEqual(draft);
    expect(props.mock.calls.at(-1)?.[0]).not.toHaveProperty("research");
  });

  it("loads a historical draft without editorial lineage", async () => {
    const draft = { id: 7, idea: "historical", editorial: null };
    stubFetch(draft);

    render(await StudioPage({ searchParams: Promise.resolve({ draft: "7" }) }));

    expect(props.mock.calls.at(-1)?.[0].initialDraft).toEqual(draft);
  });
});
