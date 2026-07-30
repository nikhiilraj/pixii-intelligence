import "@testing-library/jest-dom/vitest";

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Asset, Draft, Template } from "@/lib/api";

import Studio, {
  NONE,
  assetPayload,
  defaultAssetValues,
  draftPayload,
  imageSlots,
  noneOf,
  templateId,
} from "./Studio";

/* What is NOT tested here, deliberately: opening a Select, arrowing through it, typeahead,
 * Escape, and focus return. Those belong to Radix, are tested upstream, and jsdom only
 * approximates focus. They were exercised by hand in Chrome instead; the slice report says
 * exactly what.
 *
 * That has a consequence worth naming, because it silently weakens assertions: while a Select
 * is closed Radix renders its items into a detached DocumentFragment, so `SelectContent` is not
 * in `document.body` and `queryByText("… pick an asset")` is `null` whether the picker is on
 * the page or not. Anything checking for the picker's presence anchors on the heading or on the
 * trigger's `aria-label`, never on item text.
 *
 * What IS tested is ours, and it is one bug class: the page sending the wrong thing while
 * everything still renders. A slot name the backend ignores, a `""` or a sentinel that shadows
 * a template default, and a stale slot from the previously chosen visual are all silent — the
 * request succeeds, the draft is created, and the image is simply missing. */

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

/** Answers the two endpoints this page calls, by path. */
function stubApi() {
  const fetchStub = vi.fn((url: string) =>
    Promise.resolve(
      String(url).endsWith("/drafts/suggest")
        ? jsonResponse(200, {
            hook: { id: 1 },
            structure: { id: 2 },
            visual: { id: 3 },
            reason: "stat-hero, because the idea is a number",
          })
        : jsonResponse(201, DRAFT),
    ),
  );
  vi.stubGlobal("fetch", fetchStub);
  return fetchStub;
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

function typeIdea() {
  fireEvent.change(screen.getByPlaceholderText(/what is this post about/i), {
    target: { value: "a nine figure exit" },
  });
}

/** Fill in the idea and choose the visual — everything the picker needs to appear.
 *
 *  Driven through Suggest rather than the visual Select, for the same reason `/posts` drives its
 *  failure tests through the order-toggle Button: it is the one path to a chosen visual that is
 *  not a Radix listbox, and it reaches `chooseVisual` — the identical handler the Select's
 *  `onValueChange` calls. */
async function setUp(slots: Record<string, unknown>[], assets: Asset[] | null = LIBRARY) {
  const fetchStub = stubApi();
  render(<Studio templates={library(slots)} assets={assets} />);
  typeIdea();
  fireEvent.click(screen.getByRole("button", { name: /suggest templates/i }));
  // The reason line only renders once the suggestion has been applied, so it is the signal that
  // `chooseVisual` has run — and it renders whether or not the visual has image slots.
  await screen.findByText(/stat-hero, because/);
  return fetchStub;
}

/** The raw body of the one `POST /drafts` the click made. `/drafts/suggest` does not match. */
function sentRaw(fetchStub: ReturnType<typeof vi.fn>): string {
  const call = fetchStub.mock.calls.find(([url]) => String(url).endsWith("/drafts"));
  return String((call?.[1] as RequestInit).body);
}

/** The same body, parsed. */
function sentBody(fetchStub: ReturnType<typeof vi.fn>): Record<string, unknown> {
  return JSON.parse(sentRaw(fetchStub));
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

/* The regression this slice could plausibly have shipped. Radix will not accept an empty item
 * value, so every "let it suggest" / "pick an asset" option carries a sentinel — and the five
 * controls on this page leak it in two different ways.
 *
 * `hook_id: "__none__"` is the loud one: a 422 that names itself. The asset slots are the silent
 * one, and they are the reason this block exists — `assetPayload` filtered on `value !== ""`,
 * which a sentinel string passes. The request would then succeed, the draft would be created,
 * and the image would simply be missing with nothing on screen saying why. */
describe("the sentinel never leaves the component", () => {
  it("reads the unset template select as null, not as the sentinel string", () => {
    expect(templateId(NONE)).toBeNull();
    expect(templateId("3")).toBe(3);
  });

  it("reads the unset asset select as empty, not as the sentinel string", () => {
    expect(noneOf(NONE)).toBe("");
    expect(noneOf("7")).toBe("7");
  });

  it("builds the default-state request body with nulls and no asset values", () => {
    const body = draftPayload(
      "a nine figure exit",
      {
        hook: templateId(NONE),
        structure: templateId(NONE),
        visual: templateId(NONE),
      },
      undefined,
      {},
    );

    expect(body).toEqual({
      idea: "a nine figure exit",
      hook_id: null,
      structure_id: null,
      visual_id: null,
      asset_values: {},
    });
    expect(JSON.stringify(body)).not.toContain(NONE);
  });

  it("drops a slot holding the sentinel exactly as it drops an empty one", () => {
    // The silent leak, at the one place that can make it impossible. Both spellings of "nothing
    // is chosen here" have to reach the API as an absent key, because an absent key is what the
    // backend reads as "fall back to the template default".
    const visual = template({ slots: statHeroSlots() });

    expect(assetPayload(visual, { left_image_url: NONE })).toEqual({});
    expect(assetPayload(visual, { left_image_url: noneOf(NONE) })).toEqual({});
    expect(assetPayload(visual, { left_image_url: "" })).toEqual({});
  });

  it("sends the whole default-state body over the wire without the sentinel in it", async () => {
    // The component-level half: nothing is chosen, nothing is touched, and the request that
    // leaves has three nulls in it. Asserting the raw string as well as the parsed object,
    // because the sentinel could ride out in a key or a value and only one of those is typed.
    const fetchStub = stubApi();
    render(<Studio templates={library(statHeroSlots())} assets={LIBRARY} />);
    typeIdea();

    fireEvent.click(screen.getByRole("button", { name: /generate draft/i }));

    await waitFor(() => expect(fetchStub).toHaveBeenCalled());

    expect(sentRaw(fetchStub)).not.toContain(NONE);
    expect(sentBody(fetchStub)).toEqual({
      idea: "a nine figure exit",
      hook_id: null,
      structure_id: null,
      visual_id: null,
      asset_values: {},
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

  it("sends the asset id the API expects, as a string keyed by slot name", () => {
    // Was driven through the two slot selects; a Radix trigger is a button and cannot be driven
    // in jsdom, so the same claim is made of the function the `onValueChange` handler feeds.
    expect(
      assetPayload(template({ slots: statHeroSlots() }), {
        left_image_url: "7",
        right_image_url: "9",
      }),
    ).toEqual({ left_image_url: "7", right_image_url: "9" });
  });

  it("lets a pick override the template default", () => {
    expect(assetPayload(template({ slots: statHeroSlots(9) }), { right_image_url: "7" })).toEqual({
      right_image_url: "7",
    });
  });

  it("pre-selects a template default and sends it with nobody touching the control", async () => {
    const fetchStub = await setUp(statHeroSlots(9));

    // The trigger shows the pre-selected asset rather than the "pick an asset" placeholder —
    // which is also the proof that a Radix trigger reports its value at all, given the item it
    // reads that text from is not in the document while the list is closed.
    expect(screen.getByLabelText("right_image_url")).toHaveTextContent(/Product shot/);

    fireEvent.click(screen.getByRole("button", { name: /generate draft/i }));

    await waitFor(() => expect(sentBody(fetchStub)).toBeTruthy());
    // The default one is sent; the slot with no default is omitted, not sent empty.
    expect(sentBody(fetchStub).asset_values).toEqual({ right_image_url: "9" });
  });

  it("sends the suggested template ids as ids, not as whatever the trigger displays", async () => {
    const fetchStub = await setUp(statHeroSlots());

    fireEvent.click(screen.getByRole("button", { name: /generate draft/i }));

    await waitFor(() => expect(sentBody(fetchStub)).toBeTruthy());
    expect(sentBody(fetchStub)).toMatchObject({ hook_id: 1, structure_id: 2, visual_id: 3 });
  });
});

describe("when there is nothing to pick from", () => {
  it("says the library is empty, and why that blocks this template", async () => {
    await setUp(statHeroSlots(), []);

    expect(screen.getByText(/nothing in the library yet/i)).toBeInTheDocument();
  });

  it("does not claim the library is empty when the read failed", async () => {
    // The other direction, and the whole reason `assets` is `Asset[] | null`. Telling someone
    // to upload an asset they already have is the error-versus-empty bug in a new place.
    await setUp(statHeroSlots(), null);

    expect(screen.getByText(/could not be read/i)).toBeInTheDocument();
    expect(screen.queryByText(/nothing in the library yet/i)).not.toBeInTheDocument();
  });

  it("shows no picker at all for a text-only visual", async () => {
    await setUp([{ name: "big_number", type: "text" }]);

    // Anchored on the heading and the trigger's label, NOT on "pick an asset": that string only
    // ever lives inside a closed `SelectContent`, which Radix keeps in a detached fragment, so
    // querying for it would be absent-either-way and the assertion would pass vacuously.
    expect(screen.queryByText(/^Images —/)).not.toBeInTheDocument();
    expect(screen.queryByLabelText("left_image_url")).not.toBeInTheDocument();
  });

  it("shows one picker per image slot when there is one", async () => {
    // The positive half of the assertion above — without it, "no picker" would pass on a page
    // that never renders a picker at all.
    await setUp(statHeroSlots());

    expect(screen.getByText(/^Images —/)).toHaveTextContent("2 slots");
    expect(screen.getByLabelText("left_image_url")).toBeInTheDocument();
    expect(screen.getByLabelText("right_image_url")).toBeInTheDocument();
  });
});

describe("the produced draft", () => {
  it("names the asset that filled each slot", async () => {
    await setUp(statHeroSlots());

    fireEvent.click(screen.getByRole("button", { name: /generate draft/i }));

    // The picture is a flattened PNG, so this is the only place a reviewer can see which asset
    // is in it. `asset_values` is hand-mapped into `DraftOut`; if that mapping were missed the
    // field would be absent here and these rows would silently not render.
    await waitFor(() => expect(screen.getByText(/Pixii wordmark \(#7\)/)).toBeInTheDocument());
    expect(screen.getByText(/Product shot \(#9\)/)).toBeInTheDocument();
  });
});
