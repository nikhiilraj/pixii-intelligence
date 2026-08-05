import "@testing-library/jest-dom/vitest";

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import LinkedInImportPanel, { parsePayload } from "./LinkedInImportPanel";

/* `parsePayload` is the guard and gets most of this file. The failure it prevents is not a
 * 422 — it is the paste of the wrong shape that *validates*, because `LinkedInScrapeIn` gives
 * every field but `urn` a default, and writes a list of empty posts into the corpus that
 * extraction then learns from. That is invisible until a template proposal comes back
 * meaningless, which is why it is checked before the request rather than after it.
 */

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const POST = { urn: "urn:li:activity:1", content: "a post" };

afterEach(() => {
  vi.unstubAllGlobals();
  cleanup();
});

describe("parsePayload", () => {
  it("accepts the route's own envelope", () => {
    expect(parsePayload(JSON.stringify({ account: "monte", posts: [POST] }))).toEqual({
      kind: "ok",
      account: "monte",
      posts: [POST],
    });
  });

  it("accepts a bare array, which is what a scrape usually hands you", () => {
    expect(parsePayload(JSON.stringify([POST]))).toEqual({
      kind: "ok",
      account: null,
      posts: [POST],
    });
  });

  it("tells an empty field from an invalid one", () => {
    // Two different things to say: one is "you have not pasted yet", the other is "what you
    // pasted will not be sent".
    expect(parsePayload("   ")).toEqual({ kind: "empty" });
    expect(parsePayload("{oops")).toMatchObject({ kind: "invalid" });
  });

  it("refuses a shape that would validate into empty posts", () => {
    // The shape that motivates this whole function: an array of strings passes JSON, is a
    // list, and every backend field but `urn` has a default.
    expect(parsePayload(JSON.stringify(["a post", "another"]))).toMatchObject({
      kind: "invalid",
      reason: "post 1 is not an object",
    });
    expect(parsePayload(JSON.stringify([{ content: "no urn" }]))).toMatchObject({
      kind: "invalid",
      reason: "post 1 has no `urn`",
    });
  });

  it("names the offending post by position, so a long paste says where to look", () => {
    expect(parsePayload(JSON.stringify([POST, POST, { content: "no urn" }]))).toMatchObject({
      reason: "post 3 has no `urn`",
    });
  });

  it("refuses an empty list rather than posting a request that writes nothing", () => {
    expect(parsePayload(JSON.stringify({ posts: [] }))).toMatchObject({ kind: "invalid" });
    expect(parsePayload(JSON.stringify({ account: "monte" }))).toMatchObject({
      kind: "invalid",
      reason: expect.stringContaining("no `posts` array"),
    });
  });
});

describe("the panel", () => {
  it("blocks the action until something parses, and says which of the two states it is in", () => {
    render(<LinkedInImportPanel />);
    const button = screen.getByRole("button", { name: "Import scrape…" });

    expect(button).toBeDisabled();
    expect(screen.getByText("Paste a scrape above to enable this.")).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("scrape JSON"), { target: { value: "{oops" } });
    expect(button).toBeDisabled();
    expect(screen.getByText(/This will not be sent:/)).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("scrape JSON"), {
      target: { value: JSON.stringify({ account: "monte", posts: [POST, POST] }) },
    });
    expect(button).toBeEnabled();
    expect(screen.getByText("2 posts parsed for account monte.")).toBeInTheDocument();
  });

  it("posts the parsed payload and the media flag as set", async () => {
    const fetchSpy = vi.fn(() => Promise.resolve(jsonResponse(200, { created: 2, updated: 0 })));
    vi.stubGlobal("fetch", fetchSpy);
    render(<LinkedInImportPanel />);

    fireEvent.change(screen.getByLabelText("scrape JSON"), {
      target: { value: JSON.stringify({ account: "monte", posts: [POST] }) },
    });
    fireEvent.click(screen.getByRole("button", { name: "Import scrape…" }));
    fireEvent.click(await screen.findByRole("button", { name: "Confirm import scrape" }));

    await waitFor(() => expect(fetchSpy).toHaveBeenCalledTimes(1));
    // Media off by default: an import of text alone never touches the network.
    const [url, init] = fetchSpy.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toContain("/corpus/linkedin?with_media=false");
    expect(JSON.parse(String(init.body))).toEqual({
      account: "monte",
      posts: [POST],
    });
  });

  it("states the post count and the media decision on the confirmation", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(jsonResponse(200, { created: 0, updated: 2 }))));
    render(<LinkedInImportPanel />);

    fireEvent.change(screen.getByLabelText("scrape JSON"), {
      target: { value: JSON.stringify([POST, POST]) },
    });
    fireEvent.click(screen.getByRole("button", { name: "Import scrape…" }));

    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent("— (none named in the paste)");
    expect(dialog).toHaveTextContent("nothing leaves this machine");
  });

  it("prints a created count of zero as 0 — every post was already in the corpus", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(jsonResponse(200, { created: 0, updated: 2 }))));
    render(<LinkedInImportPanel />);

    fireEvent.change(screen.getByLabelText("scrape JSON"), {
      target: { value: JSON.stringify([POST, POST]) },
    });
    fireEvent.click(screen.getByRole("button", { name: "Import scrape…" }));
    fireEvent.click(await screen.findByRole("button", { name: "Confirm import scrape" }));

    await screen.findByRole("status");
    const value = (label: string) =>
      screen.getByText(label).nextElementSibling?.firstElementChild?.textContent;
    expect(value("Created")).toBe("0");
    expect(value("Updated")).toBe("2");
  });

  it("renders a refusal as a refusal, never as an import of zero posts", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.resolve(jsonResponse(422, { detail: "urn is required" }))),
    );
    render(<LinkedInImportPanel />);

    fireEvent.change(screen.getByLabelText("scrape JSON"), {
      target: { value: JSON.stringify([POST]) },
    });
    fireEvent.click(screen.getByRole("button", { name: "Import scrape…" }));
    fireEvent.click(await screen.findByRole("button", { name: "Confirm import scrape" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("urn is required");
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });
});
