import "@testing-library/jest-dom/vitest";

import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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
    // Unjudged is the ordinary state of a corpus row, so that is what the fixture defaults to.
    verdict: null,
    verdict_note: "",
    verdict_at: null,
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

/** One response per call, in order, and a throw on the call after the last.
 *
 *  The throw is deliberate rather than a clamp: it makes an unexpected extra request a failed
 *  test instead of a silently reused (and already-consumed) `Response`, which is what pins
 *  "the retry is one request per click" — nothing here loops or backs off on its own. */
function stubFetchSequence(...responses: Response[]) {
  let call = 0;
  const fetchStub = vi.fn((url: string) => {
    const next = responses[call++];
    if (!next) throw new Error(`unexpected fetch #${call} for ${url}`);
    return Promise.resolve(next);
  });
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
  it("reports the failure and withholds every empty-state claim", async () => {
    stubFetch(jsonResponse(422, { detail: "sort must be one of engaged_actions, impressions" }));
    render(<Explorer initial={[]} templates={[]} />);

    // The corpus-is-empty wording, because `initial` is empty. There is nothing wrong with
    // saying so *here* — the read succeeded and returned nothing.
    expect(screen.getByText("The corpus is empty.")).toBeInTheDocument();

    // The order toggle, not a Select — a plain button on the identical `apply` path, so the
    // failure handling is exercised without driving Radix in jsdom.
    fireEvent.click(screen.getByRole("button", { name: "toggle sort direction" }));

    await waitFor(() =>
      expect(toastError).toHaveBeenCalledWith("Filter failed", {
        description:
          "sort must be one of engaged_actions, impressions — the rows below are the previous result.",
      }),
    );

    /* THE assertion. The 422 is reported, with the API's own words, and neither empty state is
       on the page — an emptiness claim needs data to be about, and a failed request has none.
       Both are checked because the component picks between them: asserting only the one that
       was showing would let the other be substituted and still pass. */
    expect(screen.getByRole("alert")).toHaveTextContent("Filter failed");
    expect(screen.getByRole("alert")).toHaveTextContent(
      "sort must be one of engaged_actions, impressions",
    );
    expect(screen.queryByText("The corpus is empty.")).not.toBeInTheDocument();
    expect(screen.queryByText("No post matches those filters.")).not.toBeInTheDocument();
  });

  it("still shows the empty state when the request genuinely succeeds with no rows", async () => {
    stubFetch(jsonResponse(200, []));
    render(<Explorer initial={[post()]} templates={[]} />);

    fireEvent.click(screen.getByRole("button", { name: "toggle sort direction" }));

    await waitFor(() =>
      expect(screen.getByText("No post matches those filters.")).toBeInTheDocument(),
    );
    // The filtered wording, not the corpus-is-empty wording: `initial` has a post in it, so
    // this is the filters excluding everything and the copy names the count it excluded from.
    expect(screen.getByText(/The corpus holds 1 post\./)).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(toastError).not.toHaveBeenCalled();
  });

  /* The other half of an honest error state: the retry has to actually go back to the API.
     A "Try again" that re-renders the same failure is the more embarrassing version of the bug
     this slice is about, because it looks like it works. */
  it("re-requests when Try again is clicked, and clears the failure when the retry succeeds", async () => {
    const fetchStub = stubFetchSequence(
      jsonResponse(500, { detail: "the database went away mid-query" }),
      jsonResponse(200, [post({ id: 2, content: "the row that arrived on the retry" })]),
    );
    render(<Explorer initial={[post()]} templates={[]} />);

    fireEvent.click(screen.getByRole("button", { name: "toggle sort direction" }));
    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
    expect(fetchStub).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole("button", { name: "Try again" }));

    // Two calls, not one: the click reached `getJson` and left the machine.
    await waitFor(() => expect(fetchStub).toHaveBeenCalledTimes(2));
    // And at the same URL — a retry that quietly requested something else would satisfy a
    // call count while answering a different question.
    expect(fetchStub.mock.calls[1][0]).toBe(fetchStub.mock.calls[0][0]);

    await waitFor(() =>
      expect(
        screen.getByRole("link", { name: /the row that arrived on the retry/ }),
      ).toBeInTheDocument(),
    );
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});

/* The 35 scraped rows carry engagement and no impressions data at all, and the table used to
 * print that absence as `0` / `0.00`. What is keyed on is stated in `Explorer.tsx`: impressions
 * and engagement_rate are written only by a Zernio analytics read, they are 0 by column
 * default, and where `engaged_actions > 0` the absence is provable arithmetic rather than a
 * heuristic. The two columns are keyed *independently*, which is what the third test pins —
 * nine rows in the corpus carry a real Zernio engagement rate with no impressions figure, and
 * deriving one dash from the other would erase them. */
describe("a metric that was never measured", () => {
  it("shows a dash, not a zero, for a row carrying engagement and no impressions", () => {
    stubFetchSequence();
    render(
      <Explorer
        initial={[post({ impressions: 0, engagement_rate: 0, engaged_actions: 1240 })]}
        templates={[]}
      />,
    );

    const cells = within(screen.getAllByRole("row")[1]).getAllByRole("cell");
    // Engaged still prints its real figure — the dash is about the two unread columns only.
    expect(cells[4]).toHaveTextContent("1,240");
    expect(cells[5]).toHaveTextContent("—");
    expect(cells[6]).toHaveTextContent("—");
    expect(cells[5]).not.toHaveTextContent("0");
    expect(cells[6]).not.toHaveTextContent("0.00");
  });

  it("still prints a measured figure, so the dash is not simply always on", () => {
    stubFetchSequence();
    render(
      <Explorer initial={[post({ impressions: 5000, engagement_rate: 3.5 })]} templates={[]} />,
    );

    const cells = within(screen.getAllByRole("row")[1]).getAllByRole("cell");
    expect(cells[5]).toHaveTextContent("5,000");
    expect(cells[6]).toHaveTextContent("3.50");
  });

  // The nine-row case. If ER's dash were derived from impressions' dash this row would lose a
  // measurement Zernio actually reported — and it sits outside the default cohort, so no
  // screenshot of the landing view would show it.
  it("keeps a reported engagement rate on a row whose impressions were never read", () => {
    stubFetchSequence();
    render(
      <Explorer
        initial={[post({ impressions: 0, engagement_rate: 11.27, engaged_actions: 8 })]}
        templates={[]}
      />,
    );

    const cells = within(screen.getAllByRole("row")[1]).getAllByRole("cell");
    expect(cells[5]).toHaveTextContent("—");
    expect(cells[6]).toHaveTextContent("11.27");
  });

  it("says on the page what the dash means, rather than leaving it to be guessed", () => {
    stubFetchSequence();
    render(<Explorer initial={[post()]} templates={[]} />);

    expect(screen.getByText(/never measured, not that it was zero/)).toBeInTheDocument();
  });
});

/* Bounding the table is only honest if the page says what it is holding back and which end of
 * the sort it cut. The second assertion is the one that matters most: the aria-live row count
 * is the only confirmation a filter took effect, so it has to keep reading the filtered total
 * and not the number of rows drawn. */
describe("the bounded table", () => {
  const many = Array.from({ length: 30 }, (_, i) =>
    post({ id: i + 1, content: `post number ${i + 1}` }),
  );

  it("draws a bounded number of rows and states how many it is holding back", () => {
    stubFetchSequence();
    render(<Explorer initial={many} templates={[]} />);

    // 25 rows plus the header.
    expect(screen.getAllByRole("row")).toHaveLength(26);
    expect(screen.getByText(/Showing 25 of 30/)).toBeInTheDocument();
    // Which end was cut. A truncated sort with no stated order is the silent version.
    expect(screen.getByText(/sorted by engaged actions, highest first/)).toBeInTheDocument();
  });

  it("keeps the live row count on the filtered total, not on the rows drawn", () => {
    stubFetchSequence();
    render(<Explorer initial={many} templates={[]} />);

    expect(screen.getByText("30 posts")).toBeInTheDocument();
  });

  it("says '1 post', not '1 posts'", () => {
    // The only count that can catch the plural: "30 posts" reads the same with the singular
    // branch deleted, so the assertion above passed on a component that never pluralises.
    stubFetchSequence();
    render(<Explorer initial={[post()]} templates={[]} />);

    // `getByText` with a string is an exact match, so "1 posts" does not satisfy it.
    expect(screen.getByText("1 post")).toBeInTheDocument();
  });

  it("shows the rest on request", () => {
    stubFetchSequence();
    render(<Explorer initial={many} templates={[]} />);

    fireEvent.click(screen.getByRole("button", { name: "Show all 30" }));

    expect(screen.getAllByRole("row")).toHaveLength(31);
    expect(screen.getByText(/Showing all 30 of 30/)).toBeInTheDocument();
  });

  it("says nothing about a bound it is not applying", () => {
    stubFetchSequence();
    render(<Explorer initial={[post()]} templates={[]} />);

    expect(screen.queryByText(/Showing/)).not.toBeInTheDocument();
  });
});

/* The corpus runs 2024-01-25 → 2026-07-17 and one formatter feeds both the chart axis and this
 * column. Asserted through the table only: recharts measures itself, and `ResponsiveContainer`
 * is 0×0 in jsdom, so the axis renders nothing to assert on. The axis is checked in a browser
 * instead — same function, so the table is the honest place to pin it.
 *
 * Timestamps are midday UTC so a machine's local offset cannot roll either date across a year
 * boundary and make the result depend on where the test runs. */
/* The view opens scoped to our own account, and that is a decision rather than a default:
   creator reference posts run an order of magnitude above ours (4331 and 1195 engaged actions
   against our best at 185), so an unscoped landing view ranks somebody else's posts as our top
   performers. Every fixture above is on our account, so removing the scope changed nothing and
   the decision was untested — measured. */
describe("the cohort the corpus opens on", () => {
  const ours = post({ id: 1, content: "one of ours", engaged_actions: 185 });
  const theirs = post({
    id: 2,
    content: "a creator reference post",
    account_username: "Creator inspiration",
    engaged_actions: 4331,
  });

  it("shows our own posts only, with the creator posts one filter away", () => {
    stubFetchSequence();
    render(<Explorer initial={[ours, theirs]} templates={[]} />);

    expect(screen.getByRole("link", { name: /one of ours/ })).toBeInTheDocument();
    // The row that would otherwise head the table on engaged actions.
    expect(screen.queryByRole("link", { name: /a creator reference post/ })).not.toBeInTheDocument();
    // And the count agrees with what is drawn, rather than reporting the whole corpus.
    expect(screen.getByText("1 post")).toBeInTheDocument();
  });
});

describe("the Published column", () => {
  it("tells apart two posts on the same day and month in different years", () => {
    // No responses: any fetch is an unexpected call and fails the test rather than reaching
    // the network. The first render does not fetch — only `apply` does.
    stubFetchSequence();
    render(
      <Explorer
        initial={[
          post({ id: 1, published_at: "2024-06-15T12:00:00Z" }),
          post({ id: 2, published_at: "2026-06-15T12:00:00Z" }),
        ]}
        templates={[]}
      />,
    );

    const published = screen
      .getAllByRole("row")
      .slice(1)
      .map((row) => within(row).getAllByRole("cell")[3].textContent);

    expect(published).toHaveLength(2);
    // Distinct is the whole requirement — the table sorts by engagement, so these two can sit
    // adjacent, and identical text there is a wrong answer wearing the shape of a right one.
    expect(new Set(published).size).toBe(2);
    // Still a readable date and not, say, a raw ISO string that happens to be distinct.
    expect(published.every((p) => p?.includes("Jun"))).toBe(true);
  });
});
