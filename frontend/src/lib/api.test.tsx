import "@testing-library/jest-dom/vitest";

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import ScoreboardPage from "@/app/scoreboard/page";
import { calls, getJson, postJson, type ApiFailure } from "@/lib/api";

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

  it("keeps the spend a failed batch already paid for", async () => {
    // `POST /drafts/variants` raises 502 with an object detail: the drafts roll back with the
    // request and the money does not, so the route reports what was spent on the failure path
    // too. Rendered as "request failed (502)" — which is what happens without an object branch —
    // the reason and the spend both vanish on the one path with nothing to show for them.
    stubFetch(
      jsonResponse(502, {
        detail: { error: "the model answered with no JSON object", llm_calls: 2, image_calls: 1 },
      }),
    );

    const result = await getJson("/drafts/variants");

    expect((result as ApiFailure).message).toBe(
      "the model answered with no JSON object, llm_calls: 2, image_calls: 1",
    );
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

/* US-019. `calls` was written twice — once in Studio, once in RetopicForm — because a second
   agent held this file when the second copy was needed. Two surfaces report a spend and both
   must word it identically; the reason it is tested at all is the plural, which is the only
   branch in it and the one a reader notices. Never a price: the meter counts calls and nothing
   in this app knows what a call cost. */
describe("calls", () => {
  it("pluralises the unit against the count it was given", () => {
    expect(calls(1, "chat completion")).toBe("1 chat completion");
    expect(calls(2, "chat completion")).toBe("2 chat completions");
    // Zero is a real answer here — `/drafts/retopic` reports `image_calls: 0` when the visual
    // was not redrawn — and it takes the plural.
    expect(calls(0, "image render")).toBe("0 image renders");
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
/** A `/metrics/templates` row. The page's `Row` type is local to it, so this mirrors the shape
 *  rather than importing it — and `sample_count: 0` is the default because that is what every
 *  row in the real database reads today: no generated draft has ever been published. */
function row(overrides: Record<string, unknown> = {}) {
  return {
    family: "a2bcf8e2",
    version: 1,
    kind: "hook",
    name: "Contrarian open",
    status: "approved",
    sample_count: 0,
    total_engaged_actions: 0,
    total_impressions: 0,
    mean_engaged_actions: 0,
    sufficient: false,
    // Deliberately NOT 5. 5 is `settings.min_sample_size`'s default and the number the page
    // falls back to when the list is empty, so a fixture on 5 cannot tell "read off the row"
    // from "hardcoded" — measured: replacing the read with a literal 5 left every assertion
    // here green.
    min_sample_size: 6,
    ...overrides,
  };
}

describe("a forced 500 renders an error state, not an empty state", () => {
  it("reports the failure and withholds both empty-state claims", async () => {
    stubFetch(jsonResponse(500, { detail: "the database went away mid-query" }));

    render(await ScoreboardPage());

    expect(screen.getByRole("alert")).toHaveTextContent("Request failed — HTTP 500");
    expect(screen.getByRole("alert")).toHaveTextContent("the database went away mid-query");
    // Both, because the page chooses between them: asserting only one would let the other be
    // rendered in its place and still pass.
    expect(screen.queryByText(/No template version exists yet/i)).not.toBeInTheDocument();
    expect(
      screen.queryByText(/No generated post has been published yet/i),
    ).not.toBeInTheDocument();
  });

  it("offers a retry beside the failure", async () => {
    stubFetch(jsonResponse(500, { detail: "the database went away mid-query" }));

    render(await ScoreboardPage());

    // On a server-rendered read the retry is a reload, so what a test can honestly assert is
    // that the affordance is there and labelled. That it re-requests is asserted where a retry
    // is a real client call — `app/posts/Explorer.test.tsx`.
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });

  it("says the library is empty when the request genuinely succeeds with no rows", async () => {
    stubFetch(jsonResponse(200, []));

    render(await ScoreboardPage());

    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByText(/No template version exists yet/i)).toBeInTheDocument();
    // The other empty state is a different claim and must not double up with this one.
    expect(
      screen.queryByText(/No generated post has been published yet/i),
    ).not.toBeInTheDocument();
  });
});

/* The two empties are not the same state, and today's real state is the second one: 37 template
   proposals exist and zero generated drafts have ever gone live. Collapsing them would either
   hide a stocked library behind "nothing here" or claim a library that does not exist. */
describe("a stocked library with no published post", () => {
  it("states that the circuit has not run, and still lists every version", async () => {
    stubFetch(jsonResponse(200, [row(), row({ version: 2, name: "Numbers first" })]));

    render(await ScoreboardPage());

    expect(screen.getByText(/No generated post has been published yet/i)).toBeInTheDocument();
    expect(screen.queryByText(/No template version exists yet/i)).not.toBeInTheDocument();

    // The rows survive the empty state. All-zero sample counts say *which* versions are
    // waiting; replacing the table with a notice would be the same lie inverted.
    // Each name links to the corpus filtered to that family — a bare `/posts` renders the
    // same words and answers a different question.
    expect(screen.getByRole("link", { name: "Contrarian open" })).toHaveAttribute(
      "href",
      "/posts?template_family=a2bcf8e2",
    );
    expect(screen.getByRole("link", { name: "Numbers first" })).toBeInTheDocument();
  });

  it("drops the notice as soon as one row has evidence", async () => {
    stubFetch(
      jsonResponse(200, [
        row(),
        row({
          version: 2,
          name: "Numbers first",
          sample_count: 3,
          total_engaged_actions: 41,
          mean_engaged_actions: 13.7,
        }),
      ]),
    );

    render(await ScoreboardPage());

    expect(
      screen.queryByText(/No generated post has been published yet/i),
    ).not.toBeInTheDocument();
    // One attributed post is enough to stop the empty claim. The evidence-first view exposes
    // the raw sample count and withholds averages below the reading threshold.
    expect(screen.getByLabelText("3 published posts")).toBeInTheDocument();
    expect(screen.getByText(/Nothing here has enough posts behind it to read yet/)).toBeInTheDocument();
    expect(screen.getByText(/withholds averages/)).toBeInTheDocument();
    expect(screen.queryByText("13.7")).not.toBeInTheDocument();
  });
});
