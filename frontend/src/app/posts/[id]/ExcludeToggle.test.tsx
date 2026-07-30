import "@testing-library/jest-dom/vitest";

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import ExcludeToggle from "./ExcludeToggle";

/* US-004's other criterion: a failed mutation raises a toast, and the toast carries the API's
 * own `detail` rather than a status code. `ExcludeToggle` is the thinnest of the four migrated
 * mutations — one button, one request — and it is the call site US-003 recorded as having
 * thrown the body away entirely ("failed (404)" where the API had written "no post 999"), so
 * it is the one where losing the message again would cost the most. */

const toastError = vi.hoisted(() => vi.fn());
vi.mock("sonner", () => ({ toast: { error: toastError } }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh: vi.fn() }) }));

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function clickToggle() {
  fireEvent.click(screen.getByRole("button", { name: "Hold out of extraction" }));
}

afterEach(() => {
  vi.unstubAllGlobals();
  toastError.mockClear();
  cleanup();
});

describe("a failed mutation", () => {
  it("raises a toast carrying the API's detail, not the status code", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(jsonResponse(404, { detail: "no post 999" }))));
    render(<ExcludeToggle postId={999} excluded={false} />);

    clickToggle();

    await waitFor(() =>
      expect(toastError).toHaveBeenCalledWith("Could not change the extraction setting", {
        description: "no post 999",
      }),
    );
  });

  // FastAPI answers a validation error with `detail` as a list of objects, not a string.
  // US-003 flattens it; the toast has to carry that flattened sentence rather than
  // `[object Object]`, so the shape is asserted through the toast and not only at the helper.
  it("carries the list-shaped validation detail as a sentence", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve(
          jsonResponse(422, {
            detail: [{ loc: ["body", "excluded"], msg: "Input should be a valid boolean" }],
          }),
        ),
      ),
    );
    render(<ExcludeToggle postId={1} excluded={false} />);

    clickToggle();

    await waitFor(() =>
      expect(toastError).toHaveBeenCalledWith("Could not change the extraction setting", {
        description: "excluded: Input should be a valid boolean",
      }),
    );
  });

  it("raises no toast when the mutation succeeds", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(jsonResponse(200, { ok: true }))));
    render(<ExcludeToggle postId={1} excluded={false} />);

    clickToggle();

    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Hold out of extraction" })).toBeEnabled(),
    );
    expect(toastError).not.toHaveBeenCalled();
  });
});
