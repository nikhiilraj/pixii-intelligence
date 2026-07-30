import "@testing-library/jest-dom/vitest";

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Asset } from "@/lib/api";

import AssetLibrary, { ALL, assetPath, assetQuery, noFilter, tagList } from "./AssetLibrary";

/* What is NOT tested here, deliberately: the Dialog's focus trap, Escape, focus return, and
 * the Select's listbox and typeahead. Those belong to Radix, are tested upstream, and jsdom
 * only approximates focus — asserting them would test someone else's library and pass for the
 * wrong reasons. They were exercised by hand in a real browser; the slice report says exactly
 * what.
 *
 * What IS tested is ours: the sentinel that exists only because Radix rejects an empty item
 * value and must never reach the query, and the failure-versus-empty distinction on both the
 * upload and the read. An asset page that reports "no assets yet" while the API is refusing
 * uploads is the bug class the API layer was rewritten to remove. */

const toastError = vi.hoisted(() => vi.fn());
const toastSuccess = vi.hoisted(() => vi.fn());
vi.mock("sonner", () => ({ toast: { error: toastError, success: toastSuccess } }));

function asset(overrides: Partial<Asset> = {}): Asset {
  return {
    id: 1,
    filename: "abc123.png",
    label: "Pixii wordmark",
    kind: "logo",
    tags: ["brand"],
    width: 400,
    height: 120,
    sha256: "abc123",
    source_post_id: null,
    created_at: "2026-07-30T00:00:00Z",
    ...overrides,
  };
}

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function stubFetch(...responses: Response[]) {
  const fetchStub = vi.fn(() => Promise.resolve(responses.shift() ?? jsonResponse(200, [])));
  vi.stubGlobal("fetch", fetchStub);
  return fetchStub;
}

/** Choose a file the way the browser does — the change event carries a real `File`. */
function chooseFile(name = "wordmark.png") {
  const input = screen.getByLabelText("image file");
  fireEvent.change(input, {
    target: { files: [new File(["not really a png"], name, { type: "image/png" })] },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
  toastError.mockClear();
  toastSuccess.mockClear();
  cleanup();
});

describe("assetQuery", () => {
  it("omits every filter that is not set, rather than sending it empty", () => {
    expect(assetQuery({ kind: "", tag: "" })).toBe("");
    // No trailing `?` either: `/assets?` is a different string to hand a fetch than `/assets`.
    expect(assetPath({ kind: "", tag: "" })).toBe("/assets");
  });

  it("sends each filter under the param name the API expects", () => {
    expect(assetPath({ kind: "product", tag: "cream" })).toBe("/assets?kind=product&tag=cream");
  });

  // The regression this slice could plausibly have shipped. Radix will not accept an empty
  // item value, so "all kinds" carries a sentinel; if it reached the query the request would
  // become `?kind=__all__`, which `GET /assets` answers with a 422 listing the five kinds. The
  // one option meaning "no filter" would be the only one that errors.
  it("never lets the Select's all-values sentinel reach the query", () => {
    const query = assetQuery({ kind: noFilter(ALL), tag: "" });

    expect(query).toBe("");
    expect(query).not.toContain(ALL);
  });

  it("still passes a real selection through the same translation", () => {
    expect(assetQuery({ kind: noFilter("logo"), tag: "" })).toBe("kind=logo");
  });
});

describe("tagList", () => {
  it("splits the field into the repeated values the form fields carry", () => {
    // A single comma-joined field would store one tag called "brand,orange".
    expect(tagList(" brand, orange ,, ")).toEqual(["brand", "orange"]);
    expect(tagList("")).toEqual([]);
  });
});

describe("a failed upload", () => {
  it("surfaces the API's own detail and adds nothing to the grid", async () => {
    stubFetch(jsonResponse(422, { detail: "not a readable image: cannot identify image file" }));
    render(<AssetLibrary initial={[]} />);

    chooseFile("notes.png");
    fireEvent.click(screen.getByRole("button", { name: "Upload" }));

    await waitFor(() =>
      expect(toastError).toHaveBeenCalledWith("Upload failed", {
        description: "not a readable image: cannot identify image file",
      }),
    );
    expect(toastSuccess).not.toHaveBeenCalled();
    // The refusal did not turn into a row, and it did not turn into a success either.
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
  });

  it("reports a dedupe as a dedupe rather than as a fresh upload", async () => {
    // `POST /assets` answers 200 with the row it already had when the bytes match. Both
    // outcomes look identical in the grid, so the only place the difference can be told is
    // here — and "Added" for a file that was already stored is a false confirmation.
    const existing = asset({ id: 7, label: "already here" });
    stubFetch(jsonResponse(200, existing), jsonResponse(200, [existing]));
    render(<AssetLibrary initial={[existing]} />);

    chooseFile();
    fireEvent.click(screen.getByRole("button", { name: "Upload" }));

    await waitFor(() =>
      expect(toastSuccess).toHaveBeenCalledWith("Already in the library", {
        description: "Those exact bytes are already stored as “already here”.",
      }),
    );
  });
});

describe("a failed read", () => {
  // Both directions, as US-003 established: asserting only the failure would let "never show
  // the empty state" pass, and asserting only the success would let "always show it" pass.
  it("keeps the rows, surfaces the detail, and withholds the empty-library claim", async () => {
    stubFetch(jsonResponse(500, { detail: "database is not reachable" }));
    render(<AssetLibrary initial={[asset()]} />);

    fireEvent.change(screen.getByLabelText("filter by tag"), { target: { value: "cream" } });

    await waitFor(() =>
      expect(toastError).toHaveBeenCalledWith("Could not read the library", {
        description: "database is not reachable — the assets below are the previous result.",
      }),
    );
    // The previous result is still on screen, and no claim is made about emptiness.
    expect(screen.getByAltText("Pixii wordmark — logo")).toBeInTheDocument();
    expect(screen.queryByText(/No assets/)).not.toBeInTheDocument();
  });

  it("still says the filter matched nothing when the request genuinely succeeds empty", async () => {
    stubFetch(jsonResponse(200, []));
    render(<AssetLibrary initial={[asset()]} />);

    fireEvent.change(screen.getByLabelText("filter by tag"), { target: { value: "cream" } });

    await waitFor(() =>
      expect(screen.getByText("No assets match that filter.")).toBeInTheDocument(),
    );
    expect(toastError).not.toHaveBeenCalled();
  });

  it("lets only the newest request write, so a slow earlier one cannot win", async () => {
    // Typing "ab" is two requests. The first resolves last and carries the result for "a";
    // without the guard it lands second and the grid shows a filter the field does not say.
    const slow = jsonResponse(200, [asset({ id: 9, label: "stale result" })]);
    const fast = jsonResponse(200, [asset({ id: 1, label: "current result" })]);
    let first = true;
    vi.stubGlobal(
      "fetch",
      vi.fn(() => {
        if (first) {
          first = false;
          return new Promise<Response>((resolve) => setTimeout(() => resolve(slow), 20));
        }
        return Promise.resolve(fast);
      }),
    );
    render(<AssetLibrary initial={[]} />);

    const field = screen.getByLabelText("filter by tag");
    fireEvent.change(field, { target: { value: "a" } });
    fireEvent.change(field, { target: { value: "ab" } });

    await waitFor(() => expect(screen.getByText("current result")).toBeInTheDocument());
    await new Promise((resolve) => setTimeout(resolve, 40));
    expect(screen.queryByText("stale result")).not.toBeInTheDocument();
    expect(screen.getByText("current result")).toBeInTheDocument();
  });

  it("distinguishes an empty library from an empty filter result", () => {
    render(<AssetLibrary initial={[]} />);

    expect(screen.getByText("No assets yet — upload a logo or a product shot.")).toBeInTheDocument();
  });
});

describe("deleting", () => {
  it("asks first — the click that says Delete does not call the API", () => {
    const fetchStub = stubFetch(jsonResponse(200, { deleted: 1 }));
    render(<AssetLibrary initial={[asset()]} />);

    fireEvent.click(screen.getByRole("button", { name: "delete Pixii wordmark — logo" }));

    // The one irreversible action in the slice: the grid button opens the confirmation and
    // nothing else. If this ever calls fetch, the file is gone before anyone was asked.
    expect(fetchStub).not.toHaveBeenCalled();
    expect(screen.getByText(/deletes the file from disk/)).toBeInTheDocument();
  });

  it("names what holds a referenced asset instead of reporting a generic failure", async () => {
    stubFetch(
      jsonResponse(409, {
        detail:
          "asset 1 is still referenced by draft 21 (slot 'left_image_url'). Deleting it " +
          "removes the only copy of the file from disk.",
      }),
    );
    render(<AssetLibrary initial={[asset()]} />);

    fireEvent.click(screen.getByRole("button", { name: "delete Pixii wordmark — logo" }));
    fireEvent.click(screen.getByRole("button", { name: "Delete from disk" }));

    await waitFor(() =>
      expect(toastError).toHaveBeenCalledWith("Still in use", {
        description:
          "asset 1 is still referenced by draft 21 (slot 'left_image_url'). Deleting it " +
          "removes the only copy of the file from disk.",
      }),
    );
    // The asset is still listed, because it is still there.
    expect(screen.getByAltText("Pixii wordmark — logo")).toBeInTheDocument();
  });

  it("sends DELETE to the asset's own path once confirmed", async () => {
    const fetchStub = stubFetch(jsonResponse(200, { deleted: 1 }), jsonResponse(200, []));
    render(<AssetLibrary initial={[asset()]} />);

    fireEvent.click(screen.getByRole("button", { name: "delete Pixii wordmark — logo" }));
    fireEvent.click(screen.getByRole("button", { name: "Delete from disk" }));

    await waitFor(() => expect(fetchStub).toHaveBeenCalled());
    const [url, init] = fetchStub.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("http://localhost:8000/assets/1");
    expect(init.method).toBe("DELETE");
  });
});
