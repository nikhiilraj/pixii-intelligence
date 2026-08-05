import "@testing-library/jest-dom/vitest";

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Draft, MetricSnapshot, Post } from "@/lib/api";

import PostDetail from "./page";

/* US-009. `GET /posts/{id}/history` and `metrics.draft_for_post` both existed, were both
 * tested, and were called by nothing; this page is their reader.
 *
 * The assertion that matters most is the lineage one. `_lineage` in api_drafts.py resolves
 * `(family_id, version)` exactly, and G2 finding #3 was what happens when something resolves
 * the same question by "newest version of the family": a v2 draft redrawn after a v3 existed
 * credited v2 with v3's work, in the Zernio metadata and in `template_performance` both. A
 * lineage *display* that re-resolved would put that error back on the surface whose whole job
 * is attribution. So the test below hands the page a draft on v1 while `/templates` holds a
 * v3 of the same family, and asserts both that v1 is what renders and that `/templates` is
 * never requested at all — consuming `draft.lineage` verbatim is what makes that permanent.
 *
 * Per the frontend testing policy: our own logic only. Nothing here asserts a chart mark —
 * `ResponsiveContainer` measures 0×0 in jsdom, so recharts renders no SVG. The reading count
 * and the window live in the server component precisely so they can be read here.
 */

const VISUAL_FAMILY = "a2bcf8e2fc72437a85d141b738d6f9c2";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ refresh: vi.fn() }),
  notFound: () => {
    throw new Error("notFound");
  },
}));
vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn() } }));

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function post(overrides: Partial<Post> = {}): Post {
  return {
    id: 95,
    zernio_id: "6a303ac95f7d1751abc3034b",
    platform: "linkedin",
    content: "a published post",
    published_at: "2026-06-15T17:03:39.308000",
    platform_post_url: null,
    account_username: "Monte Desai",
    media_type: "image",
    thumbnail_url: null,
    local_media_path: null,
    is_external: true,
    excluded_from_extraction: false,
    impressions: 5747,
    reach: 3530,
    likes: 46,
    comments: 123,
    shares: 0,
    saves: 16,
    engagement_rate: 3.22,
    engaged_actions: 185,
    verdict: null,
    verdict_note: "",
    verdict_at: null,
    ...overrides,
  };
}

function draft(overrides: Partial<Draft> = {}): Draft {
  return {
    id: 21,
    idea: "Amazon sellers keep buying keyword-dense titles",
    mode: "assisted",
    hook_text: "",
    body_text: "",
    full_text: "",
    visual_values: {},
    asset_values: {},
    visual_error: null,
    visual_png: null,
    has_previous_visual: false,
    zernio_post_id: "6a289584ed8bce87c92f5466",
    revision: 1,
    // Pushed, and Zernio holds this same revision.
    pushed_revision: 1,
    lineage: {
      hook: { family: "eb93614c", version: 1, name: "open-question-then-tested" },
      structure: { family: "e8742a6d", version: 2, name: "case-study-loop" },
      // The draft is on v1 of a family whose newest row is v3. See the header comment.
      visual: { family: VISUAL_FAMILY, version: 1, name: "stat-hero" },
    },
    // Present on every `DraftOut`, so present on every fixture — these are required, not
      // optional: a fixture free to omit them lets a component ship a `?? "historical"`
      // fallback that no real response ever exercises.
      generation_stage: "ready",
      generation_error: null,
    gate_results: [],
    // Null, not an empty review: this fixture is a draft nobody verified.
    verification_result: null,
    ...overrides,
  };
}

function snapshot(capturedAt: string, engaged: number): MetricSnapshot {
  return {
    id: 90,
    post_id: 95,
    captured_at: capturedAt,
    impressions: 5747,
    reach: 3530,
    likes: 46,
    comments: 123,
    shares: 0,
    saves: 16,
    clicks: 0,
    views: 0,
    engagement_rate: 3.22,
    engaged_actions: engaged,
  };
}

type Routes = {
  post?: Post;
  /** What `GET /posts/95/draft` answers with. `null` — the wire shape of a 200 with an empty
   *  body — is the *normal* answer, not an error: 57 of 57 published posts have no draft
   *  behind them. A `Response` is how a test asks for a failure instead. */
  draft?: Draft | null | Response;
  history?: MetricSnapshot[];
  templates?: unknown[];
};

/** Dispatches on path, and `/history` is matched before `/posts/95` because the former
 *  contains the latter. Returns the mock so a test can assert what was *not* requested.
 *
 *  `/templates` is answerable here only so that the trap is actually set: the page could ask
 *  for it and get a newer version back. It never does, and the test says so. */
function stubFetch(routes: Routes) {
  // Typed with the init argument even though the dispatch ignores it: the US-014 test reads
  // the body back off `mock.calls`, and a one-argument signature makes `call[1]` a type error.
  const fetchMock = vi.fn<(url: string, init?: RequestInit) => Promise<Response>>((url) => {
    if (url.includes("/history")) return Promise.resolve(jsonResponse(200, routes.history ?? []));
    if (url.includes("/templates")) {
      return Promise.resolve(jsonResponse(200, routes.templates ?? []));
    }
    // Before `/drafts`, which it contains: falling through would answer the mutation with the
    // drafts *list* and every assertion below would read off an array.
    if (url.includes("/retopic")) {
      const made = { ...draft({ id: 42, zernio_post_id: null }), llm_calls: 1, image_calls: 1 };
      return Promise.resolve(jsonResponse(201, made));
    }
    // `/posts/95/draft`, and before the bare-post fallthrough at the bottom, whose path this
    // one contains. There is no `/drafts` branch any more and that is the slice: nothing on
    // this page reads the drafts *list*, so a stub that still answered one would let the old
    // browser-side join come back and pass.
    if (url.includes("/draft")) {
      const answer = routes.draft ?? null;
      return Promise.resolve(answer instanceof Response ? answer : jsonResponse(200, answer));
    }
    return Promise.resolve(jsonResponse(200, routes.post ?? post()));
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function renderPage() {
  return PostDetail({ params: Promise.resolve({ id: "95" }) });
}

afterEach(() => {
  vi.unstubAllGlobals();
  cleanup();
});

describe("a post with a draft behind it", () => {
  it("names the three template versions the draft was generated from", async () => {
    stubFetch({ draft: draft(), history: [snapshot("2026-07-28T21:22:25", 185)] });

    render(await renderPage());

    expect(screen.getByText("open-question-then-tested")).toBeInTheDocument();
    expect(screen.getByText("case-study-loop")).toBeInTheDocument();
    expect(screen.getByText("stat-hero")).toBeInTheDocument();
    expect(screen.getByText("v2")).toBeInTheDocument();
    expect(screen.getAllByText("v1")).toHaveLength(2);
  });

  it("reads the draft's own version, never the newest of the family", async () => {
    const fetchMock = stubFetch({
      draft: draft(),
      history: [],
      // The trap, and it is the live one: family a2bcf8e2… holds v1 (retired) and v2
      // (approved) in the database today, `GET /templates` answers with v2 alone, and draft
      // 21 is on v1. Here it is a v3 so the wrong answer would be unmistakable.
      templates: [
        { id: 9001, family_id: VISUAL_FAMILY, version: 3, kind: "visual", name: "stat-hero" },
      ],
    });

    render(await renderPage());

    // v1 is what the draft was generated from; v3 is what the family is on now.
    expect(screen.queryByText("v3")).not.toBeInTheDocument();
    expect(screen.getAllByText("v1").length).toBeGreaterThan(0);
    // And the only way that stays true is by never asking the templates route.
    const asked = fetchMock.mock.calls.map(([url]) => String(url));
    expect(asked.some((url) => url.includes("/templates"))).toBe(false);
  });

  it("reports the readings behind the curve rather than implying a measurement", async () => {
    /* Two syncs on the same day, hours apart — which is what the 6h sync interval produces and
       what the date-carrying label exists for. NOT the 21:22:25 / 21:22:26 pair this fixture
       used to carry: `readingLabel` has minute resolution, so those two really do render
       identically, and an assertion that they differ cannot pass. Worth stating rather than
       hiding, because `Readings`' own docstring implies the opposite. */
    stubFetch({
      draft: draft(),
      history: [snapshot("2026-07-28T09:15:00", 185), snapshot("2026-07-28T21:22:25", 185)],
    });

    render(await renderPage());

    // The window, both ends, and they must differ: every snapshot in the database was written
    // by syncs on one day — two of them a second apart — so a date-only label prints two
    // distinct readings identically and the caption reads "21:22 → 21:22". Dropping the time
    // from the formatter, and rendering `first` at both ends, each left this test green.
    const caption = screen.getByText(/2 readings/).textContent!;
    // Non-greedy on both ends: the flat-series sentence that follows carries its own full stop,
    // and a greedy `(.+)\.` swallows it — which made the first version of this assertion pass
    // with both ends rendering the same label. Measured, and then fixed.
    const [, from, to] = caption.match(/^2 readings, (.+?) → (.+?)\./)!;
    expect(from).not.toBe(to);
    // And both ends carry a clock time, not a bare date.
    expect(from).toMatch(/\d{2}:\d{2}/);
    expect(to).toMatch(/\d{2}:\d{2}/);
    // Both readings carry the same number, and a flat line that does not say so reads as
    // "engagement stopped" instead of "measured twice, effectively once".
    expect(screen.getByText(/unchanged across all of them/)).toBeInTheDocument();
  });

  /* US-014. Re-topic is the action lineage makes possible, so it lives with the lineage block
     and shares its gate: the route answers 409 for a post with no draft behind it, and an
     affordance that can only fail is worse than none.

     The shape of the request is pinned in RetopicForm.test.tsx; what is pinned *here* is the
     id this page hands it, which that test cannot see because it passes `postId` by hand. The
     three candidate numbers on this page are all distinct on purpose — post 95, draft 21, and
     `late_post_id` — and only one of them is what `source_post_id` means. Draft 21's id would
     be accepted by the route as a post id and would re-topic post 21, a real and unrelated
     row; `late_post_id` is the lineage join's key and belongs to a different namespace. */
  it("offers the re-topic the recorded templates make possible, against this post's own id", async () => {
    const fetchMock = stubFetch({ draft: draft(), history: [] });

    render(await renderPage());

    expect(screen.getByRole("button", { name: /^Write the new draft/ })).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText(/^The new subject/), {
      target: { value: "why keyword-dense titles stall on Walmart" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^Write the new draft/ }));

    await waitFor(() =>
      expect(fetchMock.mock.calls.some(([url]) => String(url).includes("/drafts/retopic"))).toBe(
        true,
      ),
    );
    const call = fetchMock.mock.calls.find(([url]) => String(url).includes("/drafts/retopic"))!;
    expect(JSON.parse(String(call[1]?.body))).toEqual({
      idea: "why keyword-dense titles stall on Walmart",
      source_post_id: 95,
    });
  });

  it("hands the stored ruling and the exclusion state to the forms that own them", async () => {
    /* Wiring, and it is the same class as the `source_post_id` assertion above: both forms
       take their state as props from this page, and both render plausibly with the wrong one.
       A `note=""` silently erases the reason a human wrote the moment they change the ruling —
       `VerdictForm` seeds its textarea from the prop and the route assigns the note it is
       sent, unconditionally. An `excluded={false}` offers to hold out a post already held out.
       Measured: hardcoding either left the whole suite green. */
    stubFetch({
      post: post({
        verdict: "mixed",
        verdict_note: "the opening number did the work",
        excluded_from_extraction: true,
      }),
      draft: null,
      history: [],
    });

    render(await renderPage());

    expect(screen.getByText("Recorded: Mixed")).toBeInTheDocument();
    expect(screen.getByLabelText(/^Why/)).toHaveValue("the opening number did the work");
    expect(
      screen.getByRole("button", { name: "Let back into extraction" }),
    ).toBeInTheDocument();
  });

  it("says a curve needs readings when none were ever taken", async () => {
    stubFetch({ draft: draft(), history: [] });

    render(await renderPage());

    expect(screen.getByText(/No readings/)).toBeInTheDocument();
    // Still shows what generated it — the two blocks share a gate, not a data source.
    expect(screen.getByText("stat-hero")).toBeInTheDocument();
  });
});

/* US-019. The join moved to the server, and the reason it had to is a ceiling nothing on
   screen could have shown: this page asked `GET /drafts?limit=500` and matched in the browser,
   and that list is `created_at DESC`, so what fell off the end was the *oldest* drafts —
   exactly the ones whose posts have been live longest and are most likely to be read here. A
   post would then have rendered "no draft behind this post" while its draft sat in the
   database. `GET /posts/{id}/draft` answers the same question with no list and no limit. */
/* The media block, which had no test: a `.mov` rendered as an `<img>` and a path served off
   the wrong mount both leave a broken box on the page, and 3 of the 62 files in `media/` are
   `.mp4`. Both mutations survived the whole suite. */
describe("the post's own media", () => {
  it("serves an image off the /media mount, with an alt that names the post", async () => {
    stubFetch({ post: post({ local_media_path: "a1.png" }), draft: null, history: [] });

    render(await renderPage());

    const image = screen.getByAltText("Media for post 6a303ac95f7d1751abc3034b");
    expect(image.tagName).toBe("IMG");
    expect(image).toHaveAttribute("src", "http://localhost:8000/media/a1.png");
  });

  it("renders a video as a video, not as an image that cannot load", async () => {
    stubFetch({ post: post({ local_media_path: "clip.mov" }), draft: null, history: [] });

    const { container } = render(await renderPage());

    const video = container.querySelector("video");
    expect(video).toHaveAttribute("src", "http://localhost:8000/media/clip.mov");
    expect(container.querySelector("figure img")).toBeNull();
  });
});

describe("which request answers the lineage question", () => {
  it("asks the route for this post's draft, and never for the drafts list", async () => {
    const fetchMock = stubFetch({ draft: draft(), history: [] });

    render(await renderPage());

    const asked = fetchMock.mock.calls.map(([url]) => String(url));
    expect(asked).toContainEqual(expect.stringMatching(/\/posts\/95\/draft$/));
    // The negative is the slice. A page that asks for both would render identically.
    expect(asked.some((url) => url.includes("/drafts?"))).toBe(false);
    expect(asked.some((url) => url.includes("limit="))).toBe(false);
  });
});

describe("a post with no draft behind it", () => {
  /* The route's normal answer, not its failure: a 200 whose body is `null`, which is what all
     57 published posts here produce. `ok: true` with `data === null` must read as "nothing
     generated this", never as a failed request — that conflation, in the other direction, is
     what the third block below exists to prevent. */
  it("renders neither block and says why, rather than an empty chart", async () => {
    stubFetch({ draft: null, history: [] });

    render(await renderPage());

    expect(screen.getByText(/came from the corpus/)).toBeInTheDocument();
    expect(screen.queryByText("Generated from")).not.toBeInTheDocument();
    expect(screen.queryByText(/readings/)).not.toBeInTheDocument();
    expect(screen.queryByText("stat-hero")).not.toBeInTheDocument();
    // And no re-topic: `POST /drafts/retopic` answers 409 for exactly this post, so the button
    // could only ever fail, and it would spend the user a click to find that out.
    expect(screen.queryByRole("button", { name: /^Write the new draft/ })).not.toBeInTheDocument();
  });

  it("reads a null body as an answer, not as a failure", async () => {
    const fetchMock = stubFetch({ draft: null, history: [] });

    render(await renderPage());

    // The other half of the same assertion, and it is the one that catches treating `null` as
    // an error: no failure notice anywhere, and the history route never asked about a post
    // with nothing to attribute a curve to.
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.queryByText(/Request failed/i)).not.toBeInTheDocument();
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes("/history"))).toBe(false);
  });
});

/* The bug US-003 exists for, at the one place this app still calls `notFound()`: only a real
   404 is a missing post. Every other failure used to arrive as `null` and rendered "this page
   could not be found", which is a wrong answer wearing the shape of a right one. Nothing tested
   it — every fixture in this file answers the post read with a 200, so widening the predicate
   to any HTTP failure left the suite green. */
describe("when the post itself cannot be read", () => {
  it("reports a 500 rather than claiming the post does not exist", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.resolve(jsonResponse(500, { detail: "the database went away" }))),
    );

    render(await renderPage());

    expect(screen.getByRole("alert")).toHaveTextContent("Request failed — HTTP 500");
    expect(screen.getByRole("alert")).toHaveTextContent("the database went away");
    // The back link survives, because it is the one thing that still works.
    expect(screen.getByRole("link", { name: "← Corpus" })).toBeInTheDocument();
  });

  it("still routes a genuine 404 to the not-found UI", async () => {
    // The other direction, and without it the test above passes on a page that never calls
    // `notFound()` at all. The mock at the top of this file throws for it.
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.resolve(jsonResponse(404, { detail: "no post 95" }))),
    );

    await expect(renderPage()).rejects.toThrow("notFound");
  });
});

describe("when the draft read fails", () => {
  /* The error-vs-empty class this codebase keeps paying for: a failed read is not evidence
     that no draft exists, and "nothing generated this post" is a claim. */
  it("reports the failure instead of claiming the post has no lineage", async () => {
    stubFetch({ draft: jsonResponse(500, { detail: "drafts table is on fire" }) });

    render(await renderPage());

    expect(screen.getByText("drafts table is on fire")).toBeInTheDocument();
    expect(screen.queryByText(/came from the corpus/)).not.toBeInTheDocument();
  });
});
