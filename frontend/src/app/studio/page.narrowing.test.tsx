import { render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import StudioPage from "./page";

/* One claim, and it needs its own file because proving it means replacing `Studio`.
 *
 * `GET /drafts` answers with every draft's full `DraftOut`, base64 PNG included — 530KB over
 * the rows in the database today — and `page.tsx` maps it down to three fields before handing
 * it to a client component. What that saves is the **RSC payload**, which no jsdom render
 * produces: `Studio` never renders a draft's PNG in the list, so `container.innerHTML` cannot
 * contain the base64 whether the narrowing happens or not. The assertion that used to guard
 * this was therefore unfalsifiable — measured, by replacing the map with `{ ...d }` and
 * watching the suite stay green.
 *
 * What *is* observable here is the boundary itself: the object handed across it. So `Studio`
 * is replaced with a spy, and the assertion is that the prop carries exactly three keys.
 * Anything else it carries would be serialized into the page.
 */
const props = vi.hoisted(() => vi.fn());
vi.mock("./Studio", () => ({
  default: (p: Record<string, unknown>) => {
    props(p);
    return null;
  },
}));

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const HEAVY = {
  id: 424,
  idea: "Amazon bundles might be the anti-coupon strategy",
  mode: "directed",
  hook_text: "Bundles are the anti-coupon.",
  body_text: "Same margin, no discount habit.",
  full_text: "Bundles are the anti-coupon.\n\nSame margin, no discount habit.",
  visual_values: { big_number: "$325M" },
  asset_values: { left_image_url: "7" },
  visual_error: null,
  visual_png: "iVBORw0KGgoAAAANSUhEUg",
  zernio_post_id: null,
  lineage: { hook: null, structure: null, visual: null },
};

describe("what crosses into the client component", () => {
  it("hands the drafts list three fields per row and nothing else", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string) => {
        const path = String(url).replace(/^https?:\/\/[^/]+/, "");
        if (path === "/drafts") return Promise.resolve(jsonResponse(200, [HEAVY]));
        if (path === "/templates") return Promise.resolve(jsonResponse(200, []));
        if (path === "/assets") return Promise.resolve(jsonResponse(200, []));
        if (path === "/health")
          return Promise.resolve(
            jsonResponse(200, { status: "ok", database: true, credentials: {}, variants_max: 7 }),
          );
        return Promise.reject(new Error(`unexpected request: ${path}`));
      }),
    );

    // Rendered, not just awaited: the element has to be mounted for the spy to be called.
    render(await StudioPage({ searchParams: Promise.resolve({}) }));

    const drafts = props.mock.calls[0][0].drafts as Record<string, unknown>[];
    // Exactly these keys — `toEqual` on the row, so an extra field is a failure rather than
    // something a `toMatchObject` would wave through.
    expect(drafts).toEqual([
      { id: 424, idea: "Amazon bundles might be the anti-coupon strategy", zernio_post_id: null },
    ]);
    expect(Object.keys(drafts[0])).toHaveLength(3);

    vi.unstubAllGlobals();
  });
});
