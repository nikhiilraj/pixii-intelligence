import "@testing-library/jest-dom/vitest";

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import PromoteMedia from "./PromoteMedia";

/* `POST /assets/promote` had no caller at all: 62 files sit in `media/` and the asset library
 * starts empty, so a visual template with an `image_url` slot had nothing real to offer until
 * somebody uploaded a second copy of an image the app already held.
 *
 * Two things here are the slice rather than the wiring: the video refusal, which is said
 * before a button that could only fail, and the tag field, which is a list on the wire and
 * would otherwise post one unfilterable "a, b, c" tag.
 */

const toastError = vi.hoisted(() => vi.fn());
vi.mock("sonner", () => ({ toast: { error: toastError, success: vi.fn() } }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh: vi.fn() }) }));

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const ASSET = {
  id: 7,
  filename: "abc.png",
  label: "post-95",
  kind: "product",
  tags: ["monte"],
  width: 1080,
  height: 1350,
  sha256: "deadbeef",
  source_post_id: 95,
  created_at: "2026-08-05T09:00:00",
};

afterEach(() => {
  vi.unstubAllGlobals();
  toastError.mockClear();
  cleanup();
});

describe("media the library cannot serve", () => {
  it("says so instead of offering a button that can only fail", () => {
    // 3 of the 62 files in `media/` are `.mp4` and `local_media_path` points straight at them.
    // The route answers 422; this says it first.
    render(<PromoteMedia postId={95} isVideo />);

    expect(screen.getByText(/media is a video/)).toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });
});

describe("the confirmation", () => {
  it("does not promote until Confirm is pressed", async () => {
    const fetchSpy = vi.fn(() => Promise.resolve(jsonResponse(200, [ASSET])));
    vi.stubGlobal("fetch", fetchSpy);
    render(<PromoteMedia postId={95} isVideo={false} />);

    fireEvent.click(screen.getByRole("button", { name: "Promote this image to Assets…" }));

    expect(await screen.findByRole("dialog")).toBeInTheDocument();
    expect(fetchSpy).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Confirm promote" }));
    await waitFor(() => expect(fetchSpy).toHaveBeenCalledTimes(1));
  });

  it("posts one post id, the chosen kind and the tags as a list", async () => {
    const fetchSpy = vi.fn(() => Promise.resolve(jsonResponse(200, [ASSET])));
    vi.stubGlobal("fetch", fetchSpy);
    render(<PromoteMedia postId={95} isVideo={false} />);

    fireEvent.click(screen.getByRole("button", { name: "Promote this image to Assets…" }));
    fireEvent.change(await screen.findByLabelText("asset kind"), { target: { value: "screenshot" } });
    fireEvent.change(screen.getByLabelText("tags"), { target: { value: " monte , launch ," } });
    fireEvent.click(screen.getByRole("button", { name: "Confirm promote" }));

    await waitFor(() => expect(fetchSpy).toHaveBeenCalledTimes(1));
    const [, init] = fetchSpy.mock.calls[0] as unknown as [string, RequestInit];
    expect(JSON.parse(String(init.body))).toEqual({
      post_ids: [95],
      kind: "screenshot",
      // Split, trimmed, and the trailing empty one dropped — a single "monte , launch ," tag
      // is a tag nobody can filter on.
      tags: ["monte", "launch"],
    });
  });

  it("cannot be submitted twice while the request is in flight", async () => {
    const fetchSpy = vi.fn(() => new Promise<Response>(() => {}));
    vi.stubGlobal("fetch", fetchSpy);
    render(<PromoteMedia postId={95} isVideo={false} />);

    fireEvent.click(screen.getByRole("button", { name: "Promote this image to Assets…" }));
    fireEvent.click(await screen.findByRole("button", { name: "Confirm promote" }));

    await waitFor(() => expect(screen.getByRole("button", { name: "Promoting…" })).toBeDisabled());
    fireEvent.click(screen.getByRole("button", { name: "Promoting…" }));
    expect(fetchSpy).toHaveBeenCalledTimes(1);
  });
});

describe("afterwards", () => {
  it("names the asset it created and its real dimensions", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(jsonResponse(200, [ASSET]))));
    render(<PromoteMedia postId={95} isVideo={false} />);

    fireEvent.click(screen.getByRole("button", { name: "Promote this image to Assets…" }));
    fireEvent.click(await screen.findByRole("button", { name: "Confirm promote" }));

    // The dimensions are the file's on disk, after any downscale — not of what was uploaded.
    expect(await screen.findByText(/1080×1350/)).toBeInTheDocument();
    expect(screen.getByText("post-95")).toBeInTheDocument();
  });

  it("carries the API's own refusal into the toast", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve(jsonResponse(422, { detail: "post 95 has no downloaded media to promote" })),
      ),
    );
    render(<PromoteMedia postId={95} isVideo={false} />);

    fireEvent.click(screen.getByRole("button", { name: "Promote this image to Assets…" }));
    fireEvent.click(await screen.findByRole("button", { name: "Confirm promote" }));

    await waitFor(() =>
      expect(toastError).toHaveBeenCalledWith("Could not promote this post's media", {
        description: "post 95 has no downloaded media to promote",
      }),
    );
    // And it must not claim to have promoted anything.
    expect(screen.queryByText(/Promoted into the library/)).not.toBeInTheDocument();
  });
});
