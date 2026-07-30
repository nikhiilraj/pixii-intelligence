import "@testing-library/jest-dom/vitest";

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Asset, Draft, Template } from "@/lib/api";

import Studio, { assetPayload, defaultAssetValues, imageSlots } from "./Studio";

/* What is NOT tested here, deliberately: nothing in this file asserts a Radix behaviour,
 * because the picker is a native `<select>` — the same control the three template selects
 * beside it already use. Keyboard and screen-reader behaviour was exercised by hand; the slice
 * report says exactly what.
 *
 * What IS tested is ours, and it is one bug class: the picker sending the wrong thing while
 * everything still renders. A slot name the backend ignores, a `""` that shadows a template
 * default, or a stale slot from the previously chosen visual are all silent — the request
 * succeeds, the draft is created, and the image is simply missing. */

const toastError = vi.hoisted(() => vi.fn());
vi.mock("sonner", () => ({ toast: { error: toastError, success: vi.fn() } }));

function template(overrides: Partial<Template> = {}): Template {
  return {
    id: 1,
    family_id: "fam",
    version: 1,
    kind: "visual",
    name: "stat-hero",
    status: "approved",
    body: { renderer: "html" },
    slots: [],
    provenance: [],
    notes: "",
    ...overrides,
  };
}

/** `stat-hero` v2's slots, optionally with a default on the right-hand image. */
function statHeroSlots(defaultAssetId?: number | string): Record<string, unknown>[] {
  return [
    { name: "big_number", type: "text" },
    { name: "headline", type: "text" },
    { name: "left_image_url", type: "image_url" },
    {
      name: "right_image_url",
      type: "image_url",
      ...(defaultAssetId === undefined ? {} : { default_asset_id: defaultAssetId }),
    },
  ];
}

function asset(overrides: Partial<Asset> = {}): Asset {
  return {
    id: 7,
    filename: "abc123.png",
    label: "Pixii wordmark",
    kind: "logo",
    tags: [],
    width: 400,
    height: 120,
    sha256: "abc123",
    source_post_id: null,
    created_at: "2026-07-30T00:00:00Z",
    ...overrides,
  };
}

const LIBRARY = [asset(), asset({ id: 9, label: "Product shot", kind: "product" })];

function library(slots: Record<string, unknown>[]): Template[] {
  return [
    template({ id: 1, kind: "hook", name: "transformation" }),
    template({ id: 2, kind: "structure", name: "case-loop" }),
    template({ id: 3, kind: "visual", name: "stat-hero", slots }),
  ];
}

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const DRAFT: Draft = {
  id: 11,
  idea: "a nine figure exit",
  mode: "directed",
  hook_text: "A 9-figure exit.",
  body_text: "One main image did it.",
  full_text: "A 9-figure exit.\n\nOne main image did it.",
  visual_values: { big_number: "$325M" },
  asset_values: { left_image_url: "7", right_image_url: "9" },
  visual_error: null,
  visual_png: null,
  zernio_post_id: null,
  lineage: { hook: null, structure: null, visual: null },
};

/** Fill in the idea and choose the visual — everything the picker needs to appear. */
function setUp(slots: Record<string, unknown>[], assets: Asset[] | null = LIBRARY) {
  render(<Studio templates={library(slots)} assets={assets} />);
  fireEvent.change(screen.getByPlaceholderText(/what is this post about/i), {
    target: { value: "a nine figure exit" },
  });
  fireEvent.change(screen.getByLabelText("visual"), { target: { value: "3" } });
}

/** The parsed body of the one `POST /drafts` the click made. */
function sentBody(fetchStub: ReturnType<typeof vi.fn>): Record<string, unknown> {
  const call = fetchStub.mock.calls.find(([url]) => String(url).endsWith("/drafts"));
  return JSON.parse(String((call?.[1] as RequestInit).body));
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  toastError.mockReset();
});

describe("reading the slots off the template", () => {
  it("offers a picker only for the image_url slots", () => {
    expect(imageSlots(template({ slots: statHeroSlots() })).map((s) => s.name)).toEqual([
      "left_image_url",
      "right_image_url",
    ]);
  });

  it("asks for nothing on a text-only visual", () => {
    expect(imageSlots(template({ slots: [{ name: "big_number", type: "text" }] }))).toEqual([]);
  });

  it("leaves an untyped slot alone", () => {
    // VISUAL row 556 carries four slots with no `type` key at all. Asking for an asset there
    // would fill a slot the renderer treats as prose.
    expect(imageSlots(template({ slots: [{ name: "left_image_url" }] }))).toEqual([]);
  });

  it("reads a default written as a number and as a string as the same reference", () => {
    // `default_asset_id` is JSONB, so both shapes arrive over the wire and the backend
    // compares them as text.
    expect(defaultAssetValues(template({ slots: statHeroSlots(7) }))).toEqual({
      right_image_url: "7",
    });
    expect(defaultAssetValues(template({ slots: statHeroSlots("7") }))).toEqual({
      right_image_url: "7",
    });
  });
});

describe("what the picker sends", () => {
  it("omits an unpicked slot rather than sending an empty string", () => {
    // The backend reads an absent slot as "use the template default". A `""` would be a pick
    // of nothing, which shadows the default and leaves the slot unfillable.
    expect(assetPayload(template({ slots: statHeroSlots() }), { left_image_url: "" })).toEqual({});
  });

  it("drops a slot belonging to a visual that is no longer chosen", () => {
    expect(
      assetPayload(template({ slots: statHeroSlots() }), {
        left_image_url: "7",
        subject: "9",
      }),
    ).toEqual({ left_image_url: "7" });
  });

  it("sends the asset id the API expects, as a string keyed by slot name", async () => {
    const fetchStub = vi.fn(() => Promise.resolve(jsonResponse(201, DRAFT)));
    vi.stubGlobal("fetch", fetchStub);
    setUp(statHeroSlots());

    fireEvent.change(screen.getByLabelText("left_image_url"), { target: { value: "7" } });
    fireEvent.change(screen.getByLabelText("right_image_url"), { target: { value: "9" } });
    fireEvent.click(screen.getByRole("button", { name: /generate draft/i }));

    await waitFor(() => expect(fetchStub).toHaveBeenCalled());
    expect(sentBody(fetchStub).asset_values).toEqual({
      left_image_url: "7",
      right_image_url: "9",
    });
  });

  it("pre-selects a template default and sends it with nobody touching the control", async () => {
    const fetchStub = vi.fn(() => Promise.resolve(jsonResponse(201, DRAFT)));
    vi.stubGlobal("fetch", fetchStub);
    setUp(statHeroSlots(9));

    expect(screen.getByLabelText("right_image_url")).toHaveValue("9");

    fireEvent.click(screen.getByRole("button", { name: /generate draft/i }));

    await waitFor(() => expect(fetchStub).toHaveBeenCalled());
    // The default one is sent; the slot with no default is omitted, not sent empty.
    expect(sentBody(fetchStub).asset_values).toEqual({ right_image_url: "9" });
  });

  it("lets a pick override the template default", async () => {
    const fetchStub = vi.fn(() => Promise.resolve(jsonResponse(201, DRAFT)));
    vi.stubGlobal("fetch", fetchStub);
    setUp(statHeroSlots(9));

    fireEvent.change(screen.getByLabelText("right_image_url"), { target: { value: "7" } });
    fireEvent.click(screen.getByRole("button", { name: /generate draft/i }));

    await waitFor(() => expect(fetchStub).toHaveBeenCalled());
    expect(sentBody(fetchStub).asset_values).toEqual({ right_image_url: "7" });
  });
});

describe("when there is nothing to pick from", () => {
  it("says the library is empty, and why that blocks this template", () => {
    setUp(statHeroSlots(), []);

    expect(screen.getByText(/nothing in the library yet/i)).toBeInTheDocument();
  });

  it("does not claim the library is empty when the read failed", () => {
    // The other direction, and the whole reason `assets` is `Asset[] | null`. Telling someone
    // to upload an asset they already have is the error-versus-empty bug in a new place.
    setUp(statHeroSlots(), null);

    expect(screen.getByText(/could not be read/i)).toBeInTheDocument();
    expect(screen.queryByText(/nothing in the library yet/i)).not.toBeInTheDocument();
  });

  it("shows no picker at all for a text-only visual", () => {
    setUp([{ name: "big_number", type: "text" }]);

    expect(screen.queryByText(/pick an asset/i)).not.toBeInTheDocument();
  });
});

describe("the produced draft", () => {
  it("names the asset that filled each slot", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(jsonResponse(201, DRAFT))));
    setUp(statHeroSlots());

    fireEvent.click(screen.getByRole("button", { name: /generate draft/i }));

    // The picture is a flattened PNG, so this is the only place a reviewer can see which asset
    // is in it. `asset_values` is hand-mapped into `DraftOut`; if that mapping were missed the
    // field would be absent here and these rows would silently not render.
    await waitFor(() => expect(screen.getByText(/Pixii wordmark \(#7\)/)).toBeInTheDocument());
    expect(screen.getByText(/Product shot \(#9\)/)).toBeInTheDocument();
  });
});
