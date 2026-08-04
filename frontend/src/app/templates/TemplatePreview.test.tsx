import "@testing-library/jest-dom/vitest";

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import TemplatePreview from "./TemplatePreview";

// `@testing-library/user-event` is not a dependency of this project — TemplateManager.test.tsx
// drives every click through `fireEvent`, and this file follows the same convention rather than
// reaching for a package the codebase does not otherwise use.
const postBlob = vi.fn();
vi.mock("@/lib/api", () => ({ postBlob: (...args: unknown[]) => postBlob(...args) }));

afterEach(() => {
  cleanup();
  postBlob.mockReset();
});

describe("TemplatePreview", () => {
  it("previews the body in the editor, not a saved template", async () => {
    postBlob.mockResolvedValue({ ok: true, data: new Blob(["x"]) });
    render(
      <TemplatePreview
        body={'{"renderer":"html","html":"<h1>{headline}</h1>"}'}
        slots={[{ name: "headline", type: "text", example: "Edited" }]}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /preview/i }));

    expect(postBlob).toHaveBeenCalledWith("/templates/preview", {
      body: { renderer: "html", html: "<h1>{headline}</h1>" },
      slots: [{ name: "headline", type: "text", example: "Edited" }],
      values: { headline: "Edited" },
    });
    expect(await screen.findByAltText(/preview/i)).toBeInTheDocument();
  });

  it("says so rather than rendering nothing when the body is not JSON", () => {
    render(<TemplatePreview body="{not json" slots={[]} />);

    fireEvent.click(screen.getByRole("button", { name: /preview/i }));

    expect(postBlob).not.toHaveBeenCalled();
    expect(screen.getByText(/not valid JSON/i)).toBeInTheDocument();
  });

  it("shows the API's own message when the render is refused, not a blank pane", async () => {
    // Shape taken from `ApiFailure` in @/lib/api, not guessed: `{ ok: false, kind, message }`.
    // 501 is the real status `POST /templates/preview` answers for a non-`html` renderer
    // (api_templates.py's `preview_unsaved`), so this is the failure an editor actually hits,
    // not a synthetic one.
    postBlob.mockResolvedValue({
      ok: false,
      kind: "http",
      status: 501,
      message: "unsaved preview renders html only; save the template to preview an ai visual",
    });
    render(<TemplatePreview body={'{"renderer":"ai","prompt":"x"}'} slots={[]} />);

    fireEvent.click(screen.getByRole("button", { name: /preview/i }));

    expect(
      await screen.findByText("unsaved preview renders html only; save the template to preview an ai visual"),
    ).toBeInTheDocument();
    // No stale image left behind from a previous successful render.
    expect(screen.queryByAltText(/preview/i)).not.toBeInTheDocument();
  });
});
