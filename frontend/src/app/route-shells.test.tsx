import "@testing-library/jest-dom/vitest";

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import InboxError from "./(inbox)/error";
import InboxLoading from "./(inbox)/loading";
import InboxPage from "./(inbox)/page";
import AssetsError from "./assets/error";
import AssetsLoading from "./assets/loading";
import AssetsPage from "./assets/page";
import PostDetailError from "./posts/[id]/error";
import PostDetailLoading from "./posts/[id]/loading";
import PostDetailPage from "./posts/[id]/page";
import PostsError from "./posts/error";
import Explorer from "./posts/Explorer";
import PostsLoading from "./posts/loading";
import PostsPage from "./posts/page";
import ScoreboardError from "./scoreboard/error";
import ScoreboardLoading from "./scoreboard/loading";
import ScoreboardPage from "./scoreboard/page";
import StudioError from "./studio/error";
import StudioLoading from "./studio/loading";
import StudioPage from "./studio/page";
import TemplatesError from "./templates/error";
import TemplatesLoading from "./templates/loading";
import TemplatesPage from "./templates/page";

/* The seven `loading.tsx` / `error.tsx` pairs. Only `components/route-error.test.tsx` covered
 * any of this, and it covers the shared body — not the fourteen files that use it.
 *
 * **What a jsdom test can honestly claim here, and what it cannot.** It cannot measure a
 * skeleton: there is no layout, so "the bar is the same width as the heading it stands in for"
 * is not assertable and is not asserted. A visible layout jump needs a browser and is named as
 * unverified in the slice report.
 *
 * What it *can* hold is **correspondence** — the places where a skeleton duplicates a constant
 * from its page, which every one of these files does deliberately and says so in a comment
 * ("retyped here rather than lifted out", "it has to match exactly or the skeleton promises a
 * different table"). A duplicated constant that drifts is precisely the defect those comments
 * anticipate, and comparing the two rendered outputs is a real discriminator for it: the
 * container the page centres in, the four gate titles, the scoreboard's column grid, the
 * corpus table's headings. Getting the container wrong — `max-w-3xl` where the page is
 * `max-w-6xl` — is the visible jump, expressed as the one thing jsdom can actually compare.
 *
 * The pages are rendered against a 500 for the container comparison: every one of them renders
 * its `<main>` before it branches on the read, so the failure path reaches the shell without
 * mounting any client component.
 */

vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn() } }));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ refresh: vi.fn(), push: vi.fn() }),
  notFound: () => {
    throw new Error("notFound");
  },
}));

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function stubAll(response: () => Response) {
  vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(response())));
}

afterEach(() => {
  vi.unstubAllGlobals();
  cleanup();
});

/** The page's own `<main>`, rendered off a failed read so no client component mounts. */
async function pageMain(page: () => Promise<React.ReactElement>): Promise<HTMLElement> {
  stubAll(() => jsonResponse(500, { detail: "the database went away" }));
  const { container } = render(await page());
  return container.querySelector("main")!;
}

function loadingMain(element: React.ReactElement): HTMLElement {
  const { container } = render(element);
  return container.querySelector("main")!;
}

/** Route name -> its loading component and a call that renders its page. */
const ROUTES = [
  ["the Inbox", InboxLoading, () => InboxPage(), /Loading the Inbox/],
  ["the asset library", AssetsLoading, () => AssetsPage(), /Loading the asset library/],
  ["the corpus", PostsLoading, () => PostsPage(), /Loading the corpus/],
  [
    "a post",
    PostDetailLoading,
    () => PostDetailPage({ params: Promise.resolve({ id: "95" }) }),
    /Loading this post/,
  ],
  ["the scoreboard", ScoreboardLoading, () => ScoreboardPage(), /Loading the scoreboard/],
  [
    "Studio",
    StudioLoading,
    () => StudioPage({ searchParams: Promise.resolve({}) }),
    /Loading Studio/,
  ],
  ["the template library", TemplatesLoading, () => TemplatesPage(), /Loading the template library/],
] as const;

describe("every route's skeleton stands in the same box as its page", () => {
  it.each(ROUTES)("%s", async (_name, Loading, page) => {
    const skeleton = loadingMain(<Loading />);
    cleanup();
    const real = await pageMain(page as () => Promise<React.ReactElement>);

    /* Class list, not width: jsdom applies no stylesheet, so this compares the container the
       two files declare rather than the box either one draws. It is still the invariant that
       matters — `/posts/[id]` is deliberately `max-w-3xl` while the other six are `max-w-6xl`,
       and a skeleton on the wrong one moves the whole page sideways when the content lands.
       `aria-busy` is the one attribute the skeleton may add and the page may not. */
    expect(skeleton.className.split(" ").filter(Boolean).sort()).toEqual(
      real.className.split(" ").filter(Boolean).sort(),
    );
    expect(skeleton).toHaveAttribute("aria-busy", "true");
    expect(real).not.toHaveAttribute("aria-busy");
  });
});

describe("every route's skeleton announces itself", () => {
  it.each(ROUTES)("%s", (_name, Loading, _page, status) => {
    render(<Loading />);

    // Every `Skeleton` is `aria-hidden`, so without this line the page announces nothing at
    // all while it loads — the status text is the entire accessible content of the route.
    expect(screen.getByRole("status")).toHaveTextContent(status);
  });

  it.each(ROUTES)("%s hides its bars from the accessibility tree", (_name, Loading) => {
    const { container } = render(<Loading />);

    const bars = container.querySelectorAll(".animate-pulse");
    expect(bars.length).toBeGreaterThan(0);
    // A bar read out as content is noise; the `role="status"` line above is the announcement.
    for (const bar of bars) expect(bar).toHaveAttribute("aria-hidden");
  });
});

/* The constants a skeleton copies from its page. Each of these is retyped on purpose — the
   files say why — and each is a place where the two can silently disagree. */
describe("what a skeleton promises matches what arrives", () => {
  it("promises the Inbox's circuit and unified worklist before they arrive", async () => {
    // The redesigned home no longer promises four stacked queues. Its two stable structures are
    // the circuit overview and the oldest-first worklist; counts and rows arrive from the API.
    const { container } = render(<InboxLoading />);
    const promised = [...container.querySelectorAll("section h2")].map((h) => h.textContent);

    cleanup();
    // Dispatched, because this page reads `/inbox` and `/health` in parallel and the footer
    // iterates `credentials`.
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string) =>
        Promise.resolve(
          String(url).includes("/health")
            ? jsonResponse(200, {
                status: "ok",
                database: true,
                credentials: {},
                variants_max: 3,
              })
            : jsonResponse(200, {
                proposals_awaiting_review: { count: 0, items: [] },
                built_awaiting_push: { count: 0, items: [] },
                pushed_awaiting_monte: { count: 0, items: [] },
                published_awaiting_verdict: { count: 0, items: [] },
                closed_circuits: 0,
              }),
        ),
      ),
    );
    const page = render(await InboxPage());
    const arrived = [...page.container.querySelectorAll("section h2")].map((h) => h.textContent);

    expect(promised).toEqual(["Lineage circuit", "Work waiting on a person"]);
    expect(arrived).toEqual(promised);
  });

  it("reserves the scoreboard's observed and absent evidence bands", async () => {
    const { container } = render(<ScoreboardLoading />);
    const promised = container.querySelectorAll("section").length;

    cleanup();
    stubAll(() =>
      jsonResponse(200, [
        {
          family: "f1",
          version: 1,
          kind: "hook",
          name: "Contrarian open",
          status: "approved",
          sample_count: 0,
          total_engaged_actions: 0,
          total_impressions: 0,
          mean_engaged_actions: 0,
          sufficient: false,
          min_sample_size: 6,
        },
      ]),
    );
    const page = render(await ScoreboardPage());

    // The evidence-first scoreboard has two stable regions: versions with observations and
    // versions with none. Loading promises that shape without inventing table columns or data.
    expect(promised).toBe(2);
    expect(page.container.querySelectorAll("section")).toHaveLength(promised);
  });

  it("states the corpus table's real headings", () => {
    const headings = (root: ParentNode) =>
      [...root.querySelectorAll("th")].map((th) => th.textContent);

    const { container } = render(<PostsLoading />);
    const promised = headings(container);

    cleanup();
    const real = render(
      <Explorer
        initial={[]}
        templates={[]}
      />,
    );

    // The columns are fixed, so a bar where "Engaged" belongs would hide structure that is
    // already known — and a heading the page then renames leaves the skeleton lying.
    expect(promised).toEqual(headings(real.container));
    expect(promised).toContain("Engaged");
  });
});

/* The seven five-line `error.tsx` files. `RouteError` itself is covered in
   `components/route-error.test.tsx`; what is untested there is the part that differs between
   them — the name each one gives its route, and that it forwards `error` and `reset` at all. A
   wrapper that dropped `{...props}` would render a boundary with no message and a dead retry. */
describe("every route's error boundary names its own route and forwards its props", () => {
  const BOUNDARIES = [
    [InboxError, "The Inbox"],
    [AssetsError, "The asset library"],
    [PostsError, "The corpus"],
    [PostDetailError, "This post"],
    [ScoreboardError, "The scoreboard"],
    [StudioError, "Studio"],
    [TemplatesError, "The template library"],
  ] as const;

  it.each(BOUNDARIES)("%#: %s", (Boundary, what) => {
    const reset = vi.fn();
    render(<Boundary error={Object.assign(new Error("a row arrived with no version"), {})} reset={reset} />);

    expect(
      screen.getByRole("heading", { name: `${what} could not be shown` }),
    ).toBeInTheDocument();
    // The error object reached the body rather than being swallowed by the wrapper.
    expect(screen.getByRole("alert")).toHaveTextContent("a row arrived with no version");

    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(reset).toHaveBeenCalledTimes(1);
  });

  it("gives each route a name of its own", () => {
    const names = BOUNDARIES.map(([, what]) => what);
    // Copy-paste is how these are written, so the failure mode is two routes sharing a name and
    // a boundary telling the reader the wrong page broke.
    expect(new Set(names).size).toBe(names.length);
  });
});
