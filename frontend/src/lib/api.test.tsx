import "@testing-library/jest-dom/vitest";

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import ScoreboardPage from "@/app/scoreboard/page";
import { getJson, postJson, type ApiFailure } from "@/lib/api";

/** A response the way the backend actually answers: a JSON body with `detail`. */
function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function stubFetch(response: Response | Error) {
  const fetchStub = vi.fn(() =>
    response instanceof Error ? Promise.reject(response) : Promise.resolve(response),
  );
  vi.stubGlobal("fetch", fetchStub);
  return fetchStub;
}

afterEach(() => {
  vi.unstubAllGlobals();
  // Explicit because `globals: false`: RTL only auto-registers its cleanup when the test
  // framework's hooks are on the global object. Without this the previous test's error card
  // is still mounted, and the assertion that an error state is *absent* passes or fails on
  // leftover DOM — which is exactly the assertion that must be trustworthy here.
  cleanup();
});

describe("getJson", () => {
  it("distinguishes a 500 from an empty result, carrying status and the API's detail", async () => {
    stubFetch(jsonResponse(500, { detail: "the database went away mid-query" }));

    const result = await getJson<unknown[]>("/metrics/templates");

    expect(result).toEqual({
      ok: false,
      kind: "http",
      status: 500,
      message: "the database went away mid-query",
    });
  });

  it("distinguishes an unreachable backend from a failing one", async () => {
    stubFetch(new TypeError("fetch failed"));

    const result = await getJson("/metrics/templates");

    expect(result).toEqual({ ok: false, kind: "network", message: "fetch failed" });
  });

  it("reports an empty result as success, not as failure", async () => {
    stubFetch(jsonResponse(200, []));

    expect(await getJson<unknown[]>("/metrics/templates")).toEqual({ ok: true, data: [] });
  });

  it("renders FastAPI's list-shaped validation detail readably", async () => {
    stubFetch(
      jsonResponse(422, {
        detail: [
          {
            type: "enum",
            loc: ["query", "source"],
            msg: "Input should be 'zernio' or 'manual'",
            input: "nope",
          },
        ],
      }),
    );

    const result = await getJson("/posts?source=nope");

    expect(result).toMatchObject({
      kind: "http",
      status: 422,
      message: "source: Input should be 'zernio' or 'manual'",
    });
    // The bug this guards: `String(detail)` on the list would put [object Object] on screen.
    expect((result as ApiFailure).message).not.toContain("object Object");
  });

  it("keeps a non-JSON error body an http failure, not a network one", async () => {
    // A proxy answering 502 with HTML. Parsing the error body outside the http branch would
    // let this throw into the network branch and report a live server as unreachable.
    stubFetch(
      new Response("<html><body>502 Bad Gateway</body></html>", {
        status: 502,
        headers: { "Content-Type": "text/html" },
      }),
    );

    expect(await getJson("/metrics/templates")).toEqual({
      ok: false,
      kind: "http",
      status: 502,
      message: "request failed (502)",
    });
  });
});

describe("postJson", () => {
  it("carries the API's detail off a failed mutation", async () => {
    stubFetch(jsonResponse(409, { detail: "a retired template cannot be edited" }));

    expect(await postJson("/templates/1", { name: "x" }, "PUT")).toEqual({
      ok: false,
      kind: "http",
      status: 409,
      message: "a retired template cannot be edited",
    });
  });

  it("sends the method and body it was given", async () => {
    const fetchStub = stubFetch(jsonResponse(200, { id: 1 }));

    await postJson("/posts/1/exclude", { excluded: true });

    const [, init] = fetchStub.mock.calls[0] as unknown as [string, RequestInit];
    expect(init.method).toBe("POST");
    expect(init.body).toBe(JSON.stringify({ excluded: true }));
  });
});

/* The acceptance criterion itself, at the level it is written: not "the helper returns a
   failure object" but "the page says something went wrong instead of showing an empty
   scoreboard". The absence assertion is the real one — a page that renders the error text
   *and* the empty-state text has not fixed the bug.

   ScoreboardPage is the consumer under test because it is a single read with no client
   hooks. It is an async server component, so it is awaited to an element and then rendered;
   RTL cannot render the component type itself. */
describe("a forced 500 renders an error state, not an empty state", () => {
  it("reports the failure and withholds the empty-state copy", async () => {
    stubFetch(jsonResponse(500, { detail: "the database went away mid-query" }));

    render(await ScoreboardPage());

    expect(screen.getByRole("alert")).toHaveTextContent("Request failed — HTTP 500");
    expect(screen.getByRole("alert")).toHaveTextContent("the database went away mid-query");
    expect(
      screen.queryByText(/No template has an attributed post yet/i),
    ).not.toBeInTheDocument();
  });

  it("still shows the empty state when the request genuinely succeeds with no rows", async () => {
    stubFetch(jsonResponse(200, []));

    render(await ScoreboardPage());

    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByText(/No template has an attributed post yet/i)).toBeInTheDocument();
  });
});
