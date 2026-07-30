import "@testing-library/jest-dom/vitest";

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Post } from "@/lib/api";

import Explorer, { ALL, filterQuery, noFilter, type Filters } from "./Explorer";

/* What is NOT tested here, deliberately: opening a Select, arrowing through it, typeahead,
 * Escape, and focus return. Those belong to Radix, are tested upstream, and jsdom only
 * approximates focus — asserting them here would test someone else's library. They were
 * exercised by hand in Chrome instead; the slice report says exactly what.
 *
 * What IS tested is ours: the sentinel that only exists because Radix rejects an empty item
 * value, the query string it must never reach, and the empty-state suppression that a failed
 * filter has to keep. An accessible Select that silently stops filtering is a worse outcome
 * than a plain one that works. */

const toastError = vi.hoisted(() => vi.fn());
vi.mock("sonner", () => ({ toast: { error: toastError } }));

const BASE: Filters = {
  platform: "",
  account: "",
  family: "",
  since: "",
  sort: "engaged_actions",
  order: "desc",
};

function post(overrides: Partial<Post> = {}): Post {
  return {
    id: 1,
    zernio_id: "z1",
    platform: "linkedin",
    content: "a post",
    published_at: "2026-07-01T00:00:00Z",
    platform_post_url: null,
    account_username: "Monte Desai",
    media_type: null,
    thumbnail_url: null,
    local_media_path: null,
    is_external: true,
    excluded_from_extraction: false,
    impressions: 100,
    reach: 100,
    likes: 1,
    comments: 0,
    shares: 0,
    saves: 0,
    engagement_rate: 1,
    engaged_actions: 1,
    ...overrides,
  };
}

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function stubFetch(response: Response) {
  const fetchStub = vi.fn(() => Promise.resolve(response));
  vi.stubGlobal("fetch", fetchStub);
  return fetchStub;
}

afterEach(() => {
  vi.unstubAllGlobals();
  toastError.mockClear();
  cleanup();
});

describe("filterQuery", () => {
  it("omits every filter that is not set, rather than sending it empty", () => {
    expect(filterQuery(BASE)).toBe("sort=engaged_actions&order=desc");
  });

  it("sends each filter under the param name the API expects", () => {
    expect(
      filterQuery({
        platform: "linkedin",
        account: "Monte Desai",
        family: "a2bcf8e2",
        since: "2026-05-01",
        sort: "impressions",
        order: "asc",
      }),
    ).toBe(
      "sort=impressions&order=asc&platform=linkedin&account=Monte+Desai" +
        "&template_family=a2bcf8e2&since=2026-05-01",
    );
  });

  // The regression this slice could plausibly have shipped. Radix will not accept an empty
  // item value, so "all accounts" carries a sentinel; if that sentinel reached the query the
  // request would become `?platform=__all__`, which the API answers with an empty list. The
  // filter would then report "nothing matches" for the one option that means "no filter".
  it("never lets the Select's all-values sentinel reach the query", () => {
    const asSelected = {
      ...BASE,
      platform: noFilter(ALL),
      account: noFilter(ALL),
      family: noFilter(ALL),
    };

    const query = filterQuery(asSelected);

    expect(query).toBe("sort=engaged_actions&order=desc");
    expect(query).not.toContain(ALL);
  });

  it("still passes a real selection through the same translation", () => {
    expect(filterQuery({ ...BASE, platform: noFilter("linkedin") })).toContain(
      "platform=linkedin",
    );
  });
});

describe("a failed filter", () => {
  // Both directions, as US-003 established: asserting only the failure case would let
  // "never show the empty state" pass, and asserting only the success case would let
  // "always show it" pass.
  it("raises a toast carrying the API's detail and withholds the empty-state claim", async () => {
    stubFetch(jsonResponse(422, { detail: "sort must be one of engaged_actions, impressions" }));
    render(<Explorer initial={[]} templates={[]} />);

    expect(screen.getByText("Nothing matches those filters.")).toBeInTheDocument();

    // The order toggle, not a Select — a plain button on the identical `apply` path, so the
    // failure handling is exercised without driving Radix in jsdom.
    fireEvent.click(screen.getByRole("button", { name: "toggle sort direction" }));

    await waitFor(() =>
      expect(toastError).toHaveBeenCalledWith("Filter failed", {
        description:
          "sort must be one of engaged_actions, impressions — the rows below are the previous result.",
      }),
    );
    expect(screen.queryByText("Nothing matches those filters.")).not.toBeInTheDocument();
  });

  it("still shows the empty state when the request genuinely succeeds with no rows", async () => {
    stubFetch(jsonResponse(200, []));
    render(<Explorer initial={[post()]} templates={[]} />);

    fireEvent.click(screen.getByRole("button", { name: "toggle sort direction" }));

    await waitFor(() =>
      expect(screen.getByText("Nothing matches those filters.")).toBeInTheDocument(),
    );
    expect(toastError).not.toHaveBeenCalled();
  });
});
