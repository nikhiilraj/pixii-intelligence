import "@testing-library/jest-dom/vitest";

import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Asset, Draft, Template } from "@/lib/api";

import StudioPage from "./page";
import Studio, {
  NONE,
  assetPayload,
  defaultAssetValues,
  draftPayload,
  imageSlots,
  matches,
  nextIndex,
  noneOf,
  templateId,
} from "./Studio";

/* What is NOT tested here, deliberately: opening a Select, arrowing through it, typeahead, the
 * picker Dialog's focus trap, Escape, scroll lock and focus return. Those belong to Radix, are
 * tested upstream, and jsdom only approximates focus. They were exercised by hand in Chrome
 * instead; the slice report says exactly what.
 *
 * The grid's own arrow-key movement IS ours and is tested — as `nextIndex`, the pure function
 * the keydown handler feeds, because "which tile is next" is the part that can be wrong.
 *
 * That has a consequence worth naming, because it silently weakens assertions: while a Select
 * is closed Radix renders its items into a detached DocumentFragment, so `SelectContent` is not
 * in `document.body` and `queryByText("… pick an asset")` is `null` whether the picker is on
 * the page or not. A closed Dialog is detached the same way. Anything checking for the picker's
 * presence anchors on the heading or on the slot button's `aria-label`, never on the contents of
 * something closed.
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
  has_previous_visual: false,
  zernio_post_id: null,
  // What a publication command is confirmed against. On the wire for every draft, so it is on
  // the fixture too — `DraftOut.revision` has no default and neither does this.
  revision: 1,
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
  render(<Studio templates={library(slots)} assets={assets} drafts={[]} />);
  typeIdea();
  fireEvent.click(screen.getByRole("button", { name: /suggest templates/i }));
  // The reason line only renders once the suggestion has been applied, so it is the signal that
  // `chooseVisual` has run — and it renders whether or not the visual has image slots.
  await screen.findByText(/stat-hero, because/);
  return fetchStub;
}

/** Open one slot's picker and hand back the dialog it opened.
 *
 *  The slot button is a plain `<button>` carrying `aria-label={slot.name}` — the same anchor the
 *  Select trigger carried before US-015 — and the Dialog it opens is controlled by this page's
 *  own state, so a click is all there is to it. Everything the picker offers is queried through
 *  `within(dialog)`: once a slot is filled, the slot button and the tile in the grid carry the
 *  same asset label, and an unscoped query would match both. */
function openPickerElement(slot: string): HTMLElement {
  fireEvent.click(screen.getByLabelText(slot));
  return screen.getByRole("dialog");
}

const openPicker = (slot: string) => within(openPickerElement(slot));

/** A suggest stub that answers with a different visual each time it is called — the one way to
 *  reach `chooseVisual` twice without driving a Radix listbox. */
function stubSuggest(...visualIds: number[]) {
  const queue = [...visualIds];
  const fetchStub = vi.fn((url: string) => {
    if (!String(url).endsWith("/drafts/suggest")) return Promise.resolve(jsonResponse(201, DRAFT));
    const visual = queue.shift() ?? visualIds[visualIds.length - 1];
    return Promise.resolve(
      jsonResponse(200, {
        hook: { id: 1 },
        structure: { id: 2 },
        visual: { id: visual },
        reason: `visual ${visual}, because the idea is a number`,
      }),
    );
  });
  vi.stubGlobal("fetch", fetchStub);
  return fetchStub;
}

/** `POST /assets` answers with whatever this is given; suggest and generate answer as usual. */
function stubUpload(assetResponse: Response) {
  const fetchStub = vi.fn((url: string) => {
    const path = String(url).replace(/^https?:\/\/[^/]+/, "");
    if (path === "/assets") return Promise.resolve(assetResponse);
    if (path === "/drafts/suggest")
      return Promise.resolve(
        jsonResponse(200, {
          hook: { id: 1 },
          structure: { id: 2 },
          visual: { id: 3 },
          reason: "stat-hero, because the idea is a number",
        }),
      );
    return Promise.resolve(jsonResponse(201, DRAFT));
  });
  vi.stubGlobal("fetch", fetchStub);
  return fetchStub;
}

/** Idea typed and a visual chosen — the picker's precondition — against a caller's own stub.
 *  `setUp` does the same thing but installs `stubApi`; these are the tests that need a stub
 *  answering `POST /assets` as well. */
async function withVisual(fetchStub: ReturnType<typeof vi.fn>) {
  render(<Studio templates={library(statHeroSlots())} assets={LIBRARY} drafts={[]} />);
  typeIdea();
  fireEvent.click(screen.getByRole("button", { name: /suggest templates/i }));
  await screen.findByText(/because the idea is a number/);
  return fetchStub;
}

/** The row `POST /assets` hands back — a fresh one, or the one it already had on a dedupe. */
const UPLOADED = asset({ id: 31, label: "Fresh upload", kind: "brand" });

/** A second visual, for the change-of-visual reset.
 *
 *  It deliberately *shares* `left_image_url` with stat-hero and gives it no default, because
 *  that is the only shape that tells a reset from a merge: `assetPayload` already narrows the
 *  payload to the chosen visual's slots, so a pick belonging to a slot the new template does not
 *  declare is dropped either way and a fixture built that way would pass on both. A slot name
 *  that is valid on both templates is not dropped — a merge would send asset 7 for a slot nobody
 *  picked one for on this template. */
const HERO_SLOTS: Record<string, unknown>[] = [
  { name: "left_image_url", type: "image_url" },
  { name: "hero_image_url", type: "image_url", default_asset_id: 9 },
];

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
    render(<Studio templates={library(statHeroSlots())} assets={LIBRARY} drafts={[]} />);
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

    // Anchored, not a substring: `toHaveTextContent("2 slot")` matches "2 slots" and vice
    // versa, which is how a plural bug survives its own test. Measured — dropping the
    // singular branch left the old assertion green.
    expect(screen.getByText(/^Images —/)).toHaveTextContent(/^Images — 2 slots$/);
    expect(screen.getByLabelText("left_image_url")).toBeInTheDocument();
    expect(screen.getByLabelText("right_image_url")).toBeInTheDocument();
  });
});

/* US-015. The dropdown per slot became a dialog with a grid, a search box and an upload.
 *
 * Three of the four things asserted here are ours end to end — what the grid offers, what the
 * search leaves, and what a pick turns into in the request body. The fourth, the arrow keys, is
 * ours only as an index: moving focus is a `.focus()` call jsdom approximates, so what is tested
 * is which tile is next, not that the browser went there.
 *
 * Note the vacuity trap this slice adds. A closed Radix Dialog's content is in a detached
 * fragment exactly like a closed `SelectContent`, so `queryByText("Pixii wordmark")` is `null`
 * whether the picker was never opened or was opened and closed. Every negative assertion below
 * anchors on the slot button — which is really in the document — or on `queryByRole("dialog")`,
 * which is a real element when one is open. */
describe("the picker's search", () => {
  it("matches on the label, the kind and a tag, and ignores case", () => {
    const shot = asset({ id: 9, label: "Cooler comparison", kind: "product", tags: ["cream"] });
    const all = [asset(), shot];

    expect(matches(all, "pixii").map((a) => a.id)).toEqual([7]);
    expect(matches(all, "PRODUCT").map((a) => a.id)).toEqual([9]);
    expect(matches(all, "cream").map((a) => a.id)).toEqual([9]);
  });

  it("offers the whole library for an empty or blank query", () => {
    // The empty box is not a filter that matches nothing — which is the shape a naive
    // `includes("")` would get right by accident and a `startsWith` would get wrong.
    expect(matches(LIBRARY, "")).toHaveLength(2);
    expect(matches(LIBRARY, "   ")).toHaveLength(2);
  });

  it("narrows the grid as it is typed", async () => {
    // The component half. The tiles are real elements while the dialog is open, so this
    // absence is a real absence.
    await setUp(statHeroSlots());
    const dialog = openPicker("left_image_url");

    fireEvent.change(dialog.getByLabelText("search the library"), { target: { value: "wordmark" } });

    expect(dialog.getByRole("button", { name: /Pixii wordmark/ })).toBeInTheDocument();
    expect(dialog.queryByRole("button", { name: /Product shot/ })).not.toBeInTheDocument();
  });

  it("says nothing matched rather than looking like an empty library", async () => {
    // The same failure-versus-empty distinction the rest of this page is built on, one level
    // down: "nothing matches" and "there is nothing" are different claims about the library.
    await setUp(statHeroSlots());
    const dialog = openPicker("left_image_url");

    fireEvent.change(dialog.getByLabelText("search the library"), { target: { value: "zzz" } });

    expect(dialog.getByText(/Nothing in the library matches/)).toBeInTheDocument();
    expect(dialog.getByText(/2 assets are stored/)).toBeInTheDocument();
  });
});

describe("moving through the grid with the arrow keys", () => {
  it("steps forward and back and clamps at both ends rather than wrapping", () => {
    expect(nextIndex("ArrowRight", 0, 3)).toBe(1);
    expect(nextIndex("ArrowDown", 1, 3)).toBe(2);
    expect(nextIndex("ArrowRight", 2, 3)).toBe(2);
    expect(nextIndex("ArrowLeft", 1, 3)).toBe(0);
    expect(nextIndex("ArrowUp", 0, 3)).toBe(0);
  });

  it("enters the grid from the search field", () => {
    // `-1` is "focus is not on a tile", which is where every open starts — the search box has
    // it. A forward key from there has to land on the first tile or the grid is unreachable
    // without a mouse.
    expect(nextIndex("ArrowDown", -1, 3)).toBe(0);
    expect(nextIndex("Home", -1, 3)).toBe(0);
    expect(nextIndex("End", -1, 3)).toBe(2);
  });

  it("handles nothing else, so a key it does not own is left to the field it was typed in", () => {
    expect(nextIndex("a", 0, 3)).toBeNull();
    expect(nextIndex("Enter", 0, 3)).toBeNull();
    // An empty grid — a search matching nothing — has no tile to move to.
    expect(nextIndex("ArrowDown", -1, 0)).toBeNull();
  });
});

describe("picking from the grid", () => {
  it("offers one slot button per image slot, read off the declared type", async () => {
    // The component-level half of `imageSlots`. `logo` and `hero` are text slots, and a name
    // heuristic would put a picker on both of them — asking for an asset where the model
    // writes prose. These absences are real: a slot button is in the document when it exists.
    await setUp([
      { name: "logo", type: "text" },
      { name: "hero", type: "text" },
      { name: "left_image_url", type: "image_url" },
    ]);

    expect(screen.getByText(/^Images —/)).toHaveTextContent(/^Images — 1 slot$/);
    expect(screen.getByLabelText("left_image_url")).toBeInTheDocument();
    expect(screen.queryByLabelText("logo")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("hero")).not.toBeInTheDocument();
  });

  it("sends the picked slot and omits the untouched one", async () => {
    // The partially-picked body, which is the whole contract with the backend: an absent slot
    // is "fall back to the template default", and a `""` would be a pick of nothing that
    // shadows it. Both slots here have no default, so the second one must simply not be there.
    const fetchStub = await setUp(statHeroSlots());

    fireEvent.click(
      within(openPickerElement("left_image_url")).getByRole("button", { name: /Pixii wordmark/ }),
    );

    // The dialog closed on the pick, and the slot button now shows what is in it.
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(screen.getByLabelText("left_image_url")).toHaveTextContent("Pixii wordmark (logo)");

    fireEvent.click(screen.getByRole("button", { name: /generate draft/i }));

    await waitFor(() => expect(sentBody(fetchStub)).toBeTruthy());
    expect(sentBody(fetchStub).asset_values).toEqual({ left_image_url: "7" });
    expect(sentRaw(fetchStub)).not.toContain('"right_image_url"');
  });

  it("clears a pick back to the template's default by omitting the slot", async () => {
    // `right_image_url` defaults to asset 9, so the picker starts holding it. Clearing must
    // produce an absent key — the one value the backend reads as "use the default". A "no
    // image" option would be a lie here: there is no such state on a defaulted slot.
    const fetchStub = await setUp(statHeroSlots(9));

    const dialog = openPicker("right_image_url");
    // The dialog says what "the template's default" actually is, by name. Without it the
    // button offers to fall back to something the reader cannot see.
    expect(dialog.getByText(/Picking nothing leaves this slot/)).toHaveTextContent(
      "“Product shot”",
    );
    fireEvent.click(dialog.getByRole("button", { name: /use the template's default/i }));

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: /generate draft/i }));

    await waitFor(() => expect(sentBody(fetchStub)).toBeTruthy());
    expect(sentBody(fetchStub).asset_values).toEqual({});
  });

  it("offers no clear on a slot the template has no default for", async () => {
    // The other direction. Omitting a slot with no default is what `chosen_assets` leaves
    // absent and `fill()` raises `MissingSlotValue` for, so an option promising it would
    // promise a render that cannot happen.
    await setUp(statHeroSlots());
    const dialog = openPicker("left_image_url");

    expect(dialog.queryByRole("button", { name: /use the template's default/i })).toBeNull();
    // …and the dialog it is absent from is really open, so the absence is not vacuous.
    expect(dialog.getByRole("button", { name: /Pixii wordmark/ })).toBeInTheDocument();
  });

  it("resets to the new template's defaults when the visual changes", async () => {
    // `chooseVisual` is a handler and not an effect, so this is the assertion that it actually
    // runs on every path into it. A slot name is only meaningful against the template that
    // declares it: `left_image_url` surviving a change to a visual that has no such slot would
    // be a value the backend silently drops.
    const fetchStub = stubSuggest(3, 4);
    render(
      <Studio
        templates={[
          ...library(statHeroSlots()),
          template({ id: 4, kind: "visual", name: "hero-card", slots: HERO_SLOTS }),
        ]}
        assets={LIBRARY}
        drafts={[]}
      />,
    );
    typeIdea();

    fireEvent.click(screen.getByRole("button", { name: /suggest templates/i }));
    await screen.findByLabelText("left_image_url");
    fireEvent.click(
      within(openPickerElement("left_image_url")).getByRole("button", { name: /Pixii wordmark/ }),
    );
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

    // Suggest again, this time answering with the other visual.
    fireEvent.click(screen.getByRole("button", { name: /suggest templates/i }));
    await screen.findByLabelText("hero_image_url");

    fireEvent.click(screen.getByRole("button", { name: /generate draft/i }));
    await waitFor(() => expect(sentBody(fetchStub)).toBeTruthy());
    // The new template's own default, and not a trace of the pick made against the other one.
    expect(sentBody(fetchStub).asset_values).toEqual({ hero_image_url: "9" });
    expect(screen.getByLabelText("left_image_url")).toHaveTextContent("pick an asset");
  });
});

describe("which templates the form offers", () => {
  it("says nothing is approved when nothing is, and stops saying it when something is", () => {
    // Generation reads approved templates only, so a page that offered proposals would let
    // someone build a draft from a shape nobody has reviewed. The Select's own options live in
    // a detached fragment while it is closed and cannot be queried, so the observable is this
    // notice — which is derived from the same `approved` filter the options are.
    render(
      <Studio
        templates={library([]).map((t) => ({ ...t, status: "proposed" as const }))}
        assets={LIBRARY}
        drafts={[]}
      />,
    );
    expect(screen.getByText(/Nothing approved yet/)).toBeInTheDocument();

    cleanup();
    render(<Studio templates={library([])} assets={LIBRARY} drafts={[]} />);
    expect(screen.queryByText(/Nothing approved yet/)).not.toBeInTheDocument();
  });
});

describe("which tile the picker shows as chosen", () => {
  it("marks the asset currently in the slot, and only that one", async () => {
    // `aria-pressed` is the whole of it: every tile looks the same otherwise, so a grid that
    // marks nothing gives a returning reader no way to see what is already in the slot.
    await setUp(statHeroSlots(9));

    const dialog = openPicker("right_image_url");

    expect(dialog.getByRole("button", { name: /Product shot/ })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(dialog.getByRole("button", { name: /Pixii wordmark/ })).toHaveAttribute(
      "aria-pressed",
      "false",
    );
  });
});

describe("uploading without leaving the page", () => {
  it("posts the file as multipart and fills the slot with what came back", async () => {
    // The upload is a real write, so it is exercised against a stub here and never against the
    // live server. `kind` is asserted at its default: the control is a Radix Select and a Radix
    // listbox cannot be driven in jsdom, so what is provable here is that the field is sent at
    // all — `POST /assets` requires it and an absent one is a 422.
    const fetchStub = await withVisual(stubUpload(jsonResponse(200, UPLOADED)));

    const dialog = openPicker("left_image_url");
    fireEvent.change(dialog.getByLabelText("image file"), {
      target: { files: [new File(["not really a png"], "wordmark.png", { type: "image/png" })] },
    });
    fireEvent.click(dialog.getByRole("button", { name: /upload and use/i }));

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

    const call = fetchStub.mock.calls.find(([url]) => String(url).endsWith("/assets"));
    const body = (call?.[1] as RequestInit).body as FormData;
    expect(body).toBeInstanceOf(FormData);
    expect((body.get("file") as File).name).toBe("wordmark.png");
    expect(body.get("kind")).toBe("logo");
    // No hand-set Content-Type: one without a boundary is what FastAPI cannot parse, and it
    // would fail every upload rather than none.
    expect((call?.[1] as RequestInit).headers).toBeUndefined();

    // The asset it answered with is in the slot, and it is in the request the page then sends.
    expect(screen.getByLabelText("left_image_url")).toHaveTextContent("Fresh upload (brand)");
    fireEvent.click(screen.getByRole("button", { name: /generate draft/i }));
    await waitFor(() => expect(sentBody(fetchStub)).toBeTruthy());
    expect(sentBody(fetchStub).asset_values).toEqual({ left_image_url: "31" });
  });

  it("keeps the dialog open and says why when the upload is refused", async () => {
    // A refusal that filled the slot anyway would be the silent failure this page keeps
    // removing: a draft generated against an asset that was never stored.
    await withVisual(stubUpload(jsonResponse(422, { detail: "not a readable image" })));

    const dialog = openPicker("left_image_url");
    fireEvent.change(dialog.getByLabelText("image file"), {
      target: { files: [new File(["nope"], "notes.txt", { type: "image/png" })] },
    });
    fireEvent.click(dialog.getByRole("button", { name: /upload and use/i }));

    await waitFor(() =>
      expect(toastError).toHaveBeenCalledWith("Upload failed", {
        description: "not a readable image",
      }),
    );
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.getByLabelText("left_image_url")).toHaveTextContent("pick an asset");
  });

  it("offers the upload as the way out of an empty library", async () => {
    // The empty library keeps its own notice on the page — an empty library really is empty and
    // saying so is not the bug — but it now names the picker rather than another page.
    await setUp(statHeroSlots(), []);

    expect(screen.getByText(/nothing in the library yet/i)).toBeInTheDocument();
    const dialog = openPicker("left_image_url");
    expect(dialog.getByLabelText("image file")).toBeInTheDocument();
  });

  it("offers no picker at all when the library could not be read", async () => {
    // An upload into a library whose contents are unknown would turn a failed request into the
    // claim that the library holds exactly one thing. Anchored on the slot button, which is a
    // real element when it exists — not on anything inside a dialog that never opens.
    await setUp(statHeroSlots(), null);

    expect(screen.getByText(/could not be read/i)).toBeInTheDocument();
    expect(screen.queryByLabelText("left_image_url")).not.toBeInTheDocument();
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

  it("compares and restores the image a redraw replaced", async () => {
    const withHistory: Draft = {
      ...DRAFT,
      visual_png: "CURRENT",
      has_previous_visual: true,
    };
    const fetchStub = vi.fn(() => Promise.resolve(jsonResponse(200, withHistory)));
    vi.stubGlobal("fetch", fetchStub);

    render(
      <Studio
        templates={library([])}
        assets={LIBRARY}
        drafts={[]}
        initialDraft={withHistory}
      />,
    );

    expect(screen.getByRole("img", { name: "Generated visual" })).toHaveAttribute(
      "src",
      "data:image/png;base64,CURRENT",
    );
    expect(screen.getByRole("img", { name: "Previous visual" })).toHaveAttribute(
      "src",
      "http://localhost:8000/drafts/11/previous-visual?revision=0",
    );

    fireEvent.click(screen.getByRole("button", { name: "Restore previous visual" }));

    await waitFor(() => expect(fetchStub).toHaveBeenCalledTimes(1));
    const [url, init] = fetchStub.mock.calls[0] as unknown as [string, RequestInit];
    expect(String(url)).toMatch(/\/drafts\/11\/restore-visual$/);
    expect(init.method).toBe("POST");
    await waitFor(() =>
      expect(screen.getByRole("img", { name: "Previous visual" })).toHaveAttribute(
        "src",
        "http://localhost:8000/drafts/11/previous-visual?revision=1",
      ),
    );
  });

  it("explains that a failed redraw kept the working image", () => {
    render(
      <Studio
        templates={library([])}
        assets={LIBRARY}
        drafts={[]}
        initialDraft={{ ...DRAFT, visual_png: "WORKING", visual_error: "renderer unavailable" }}
      />,
    );

    expect(screen.getByRole("img", { name: "Generated visual" })).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent(
      "Redraw failed, so the last working visual is still in place: renderer unavailable",
    );
  });

  it("does not offer a comparison before a visual has been replaced", () => {
    render(
      <Studio templates={library([])} assets={LIBRARY} drafts={[]} initialDraft={DRAFT} />,
    );

    expect(screen.queryByRole("img", { name: "Previous visual" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Restore previous visual" })).not.toBeInTheDocument();
  });
});

/* US-012. Six drafts sat in the database with `GET /drafts` and `GET /drafts/{id}` both tested,
 * both working and both called by nothing, while the Inbox linked two of its queues at a bare
 * `/studio` that was empty on every visit.
 *
 * The bug class here is the one this app keeps removing: a column that is empty for one reason
 * rendering as though it were empty for another. "Nothing asked for" and "we looked and it is
 * not there" are different claims, so the negative assertions anchor on the empty state's own
 * words — which are really in the document — and never on a Select's contents. */
const LOADED: Draft = {
  ...DRAFT,
  id: 424,
  idea: "Amazon bundles might be the anti-coupon strategy",
  full_text: "Bundles are the anti-coupon.\n\nSame margin, no discount habit.",
};

const PUSHED: Draft = { ...LOADED, id: 555, zernio_post_id: "6a695286ead2fabfa56f3c27" };

function pushButton(): HTMLElement {
  return screen.getByRole("button", { name: /zernio/i });
}

describe("opening a draft that already exists", () => {
  it("shows the draft it was given instead of the empty state", () => {
    render(<Studio templates={library([])} assets={LIBRARY} drafts={[]} initialDraft={LOADED} />);

    expect(screen.getByText(/Bundles are the anti-coupon/)).toBeInTheDocument();
    expect(screen.queryByText("No draft yet.")).not.toBeInTheDocument();
  });

  it("pushes the loaded draft's own id, not the session's", async () => {
    // The assertion that discriminates "this draft" from "some draft": a label-only test passes
    // on a component that posts to the wrong id, and pushing the wrong row is not recoverable —
    // it creates a Zernio draft for a post nobody was looking at.
    const fetchStub = stubApi();
    render(<Studio templates={library([])} assets={LIBRARY} drafts={[]} initialDraft={LOADED} />);

    fireEvent.click(pushButton());

    await waitFor(() => expect(fetchStub).toHaveBeenCalled());
    // `stubApi`'s mock is typed by its one declared argument, so the init object is read the
    // same way `sentRaw` above reads it.
    const [url, init] = fetchStub.mock.calls[0] as unknown as [string, RequestInit];
    expect(String(url)).toMatch(/\/drafts\/424\/push$/);
    expect(init.method).toBe("POST");
  });

  it("cannot push a draft that is already in Zernio", () => {
    // The constraint the whole page is built around, at the one place US-012 could have broken
    // it: a loaded draft carrying a `zernio_post_id` must arrive with the button already spent.
    render(<Studio templates={library([])} assets={LIBRARY} drafts={[]} initialDraft={PUSHED} />);

    expect(pushButton()).toBeDisabled();
    expect(pushButton()).toHaveTextContent("In Zernio");
    // Where the draft is, said once. The line this replaces read "In Zernio as a draft" and sat
    // under the same condition as the panel, so it would have gone false above a history row
    // saying the post was scheduled — see the comment where it used to be.
    expect(screen.getByText(/This draft is in Zernio as post/)).toBeInTheDocument();
  });

  it("offers no publication controls until there is a post in Zernio to command", () => {
    /* Every route behind the panel updates an existing post and none of them creates one, so
     * before a push the three buttons are three ways to earn a 409. The negative anchors on a
     * button name that really is in the document once a draft is pushed — see the test below,
     * which is the same assertion the other way round. */
    render(<Studio templates={library([])} assets={LIBRARY} drafts={[]} initialDraft={LOADED} />);

    expect(screen.queryByRole("button", { name: /^publish now…$/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^schedule…$/i })).not.toBeInTheDocument();
  });

  it("offers schedule, publish and cancel for a draft that is in Zernio", () => {
    render(
      <Studio
        templates={library([])}
        assets={LIBRARY}
        drafts={[]}
        initialDraft={PUSHED}
        publications={[]}
      />,
    );

    expect(screen.getByRole("button", { name: /^schedule…$/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^publish now…$/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^cancel schedule…$/i })).toBeInTheDocument();
  });

  it("does not lend one draft's command history to another draft", async () => {
    /* The panel seeds its history into state, and the history is what stops a post being
     * commanded twice. Generating a second draft while `?draft=555` is open must not leave 555's
     * commands on screen under the new draft's id — a `key` on the draft id is what prevents it,
     * and a component reconciled onto the old instance would show the wrong audit trail beside a
     * live Publish button. */
    // The second draft is pushed too, deliberately. Answering with an unpushed draft would
    // unmount the panel outright and the assertion would then pass against a component with no
    // `key` at all — the mutation would survive and the test would look green.
    const fetchStub = vi.fn(() =>
      Promise.resolve(jsonResponse(201, { ...PUSHED, id: 777, zernio_post_id: "b0b0b0b0" })),
    );
    vi.stubGlobal("fetch", fetchStub);
    render(
      <Studio
        templates={library([])}
        assets={LIBRARY}
        drafts={[]}
        initialDraft={PUSHED}
        publications={[
          {
            id: 1,
            draft_id: 555,
            draft_revision: 1,
            action: "schedule",
            requested_local_time: "2026-08-12T09:00:00",
            timezone: "Asia/Kolkata",
            scheduled_utc: "2026-08-12T03:30:00",
            state: "accepted",
            attempts: 1,
            last_error: null,
            created_at: "2026-08-05T10:00:00",
            accepted_at: "2026-08-05T10:00:01",
          },
        ]}
      />,
    );
    expect(screen.getByText(/Asia\/Kolkata/)).toBeInTheDocument();

    typeIdea();
    fireEvent.click(screen.getByRole("button", { name: "Generate draft" }));

    await waitFor(() => expect(fetchStub).toHaveBeenCalled());
    // Draft 777 is in Zernio, so the panel is still on screen with its three buttons — and its
    // history must be draft 777's, which is empty, not draft 555's schedule.
    await waitFor(() =>
      expect(screen.getByText(/Nothing has been commanded/i)).toBeInTheDocument(),
    );
    expect(screen.queryByText(/Asia\/Kolkata/)).not.toBeInTheDocument();
  });

  it("says the draft is missing rather than rendering the empty state", () => {
    render(
      <Studio
        templates={library([])}
        assets={LIBRARY}
        drafts={[]}
        missing="HTTP 404: no draft 999"
      />,
    );

    expect(screen.getByRole("alert")).toHaveTextContent("no draft 999");
    // The conflation, asserted directly. This string is really in the document when the empty
    // state renders, so its absence is a real absence.
    expect(screen.queryByText("No draft yet.")).not.toBeInTheDocument();
  });

  it("shows a draft generated on a bad ?draft= url instead of going on reporting the 404", async () => {
    // `missing` is a prop and never clears, but the left column keeps working on that route.
    // Land on `?draft=999` from a stale link, type an idea, Generate: a row is written. A
    // column still reporting "no draft 999" would be describing a failure that a real write
    // has just contradicted — the same class of lie as an error rendering as an empty state.
    const fetchStub = stubApi();
    render(
      <Studio
        templates={library(statHeroSlots())}
        assets={LIBRARY}
        drafts={[]}
        missing="HTTP 404: no draft 999"
      />,
    );
    typeIdea();

    fireEvent.click(screen.getByRole("button", { name: /generate draft/i }));

    await waitFor(() => expect(fetchStub).toHaveBeenCalled());
    await waitFor(() => expect(screen.getByText(/A 9-figure exit/)).toBeInTheDocument());
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});

describe("the drafts list", () => {
  const SUMMARIES = [
    { id: 424, idea: "Amazon bundles might be the anti-coupon strategy", zernio_post_id: null },
    { id: 555, idea: "about cat on moon hypothesis", zernio_post_id: "6a695286" },
  ];

  it("links every draft to its own url", () => {
    render(<Studio templates={library([])} assets={LIBRARY} drafts={SUMMARIES} />);

    expect(screen.getByRole("link", { name: /Amazon bundles/ })).toHaveAttribute(
      "href",
      "/studio?draft=424",
    );
    expect(screen.getByRole("link", { name: /cat on moon/ })).toHaveAttribute(
      "href",
      "/studio?draft=555",
    );
  });

  it("marks the one that is open", () => {
    render(
      <Studio
        templates={library([])}
        assets={LIBRARY}
        drafts={SUMMARIES}
        initialDraft={{ ...LOADED, id: 555 }}
      />,
    );

    expect(screen.getByRole("link", { name: /cat on moon/ })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(screen.getByRole("link", { name: /Amazon bundles/ })).not.toHaveAttribute(
      "aria-current",
    );
  });

  it("does not claim there are no drafts when the list could not be read", () => {
    // `assets: Asset[] | null` all over again, and it costs more here: "no draft has been
    // generated yet" on a failed read is this page asserting the database is empty while six
    // drafts sit in it.
    render(<Studio templates={library([])} assets={LIBRARY} drafts={null} />);

    expect(screen.getByText(/could not be read/i)).toBeInTheDocument();
    expect(screen.queryByText(/No draft has been generated yet/i)).not.toBeInTheDocument();
  });

  it("says the list is empty when it really is", () => {
    render(<Studio templates={library([])} assets={LIBRARY} drafts={[]} />);

    expect(screen.getByText(/No draft has been generated yet/i)).toBeInTheDocument();
  });
});

/* US-013. One idea written N ways, side by side, keep one and delete the rest.
 *
 * Two things are being defended here and neither is visible in a screenshot. The first is that
 * nothing ranks: the variants are rendered in the order the response listed them and in no other
 * order, so the fixture ids are deliberately NOT ascending — `[13, 11, 12]` fails an accidental
 * sort by id, where `[11, 12, 13]` would pass one. A negative query for "best" or "recommended"
 * proves nothing; it passes on any page, including one that ranks.
 *
 * The second is what leaves in the request bodies. `POST /drafts/variants` takes the idea alone
 * and ignores the picker on purpose, but pydantic ignores unknown keys, so sending this page's
 * `draftPayload` would succeed silently and look identical. And `keep` is exact: the backend
 * 422s when an id is in both lists and 409s — deleting nothing — when a discard is in Zernio, so
 * the discard list is asserted whole, from a click on the MIDDLE variant, which is the one a
 * `slice(1)` gets wrong. */
function variant(id: number, overrides: Partial<Draft> = {}): Draft {
  return {
    ...DRAFT,
    id,
    lineage: {
      hook: { family: "fam-hook", version: 2, name: `hook-${id}` },
      structure: { family: "fam-struct", version: 1, name: `structure-${id}` },
      visual: { family: "fam-vis", version: 3, name: `visual-${id}` },
    },
    ...overrides,
  };
}

/** Generation order, and not id order — see the block comment. */
const BATCH = {
  variants: [
    variant(13, { full_text: "Bundles are the anti-coupon." }),
    variant(11, { full_text: "Coupons train the habit." }),
    variant(12, { full_text: "Same margin, no discount." }),
  ],
  llm_calls: 3,
  image_calls: 3,
};

/** Answers suggest, variants, keep and the plain generate, by exact path. */
function stubVariants(keepResponse?: Response) {
  const fetchStub = vi.fn((url: string) => {
    const path = String(url).replace(/^https?:\/\/[^/]+/, "");
    if (path === "/drafts/suggest")
      return Promise.resolve(
        jsonResponse(200, {
          hook: { id: 1 },
          structure: { id: 2 },
          visual: { id: 3 },
          reason: "stat-hero, because the idea is a number",
        }),
      );
    if (path === "/drafts/variants") return Promise.resolve(jsonResponse(201, BATCH));
    if (path === "/drafts/variants/keep")
      return Promise.resolve(keepResponse ?? jsonResponse(200, BATCH.variants[2]));
    return Promise.resolve(jsonResponse(201, DRAFT));
  });
  vi.stubGlobal("fetch", fetchStub);
  return fetchStub;
}

/** The parsed body of the one request to exactly this path. `/drafts/variants` must not match
 *  `/drafts/variants/keep`, which is why this compares the whole path rather than a suffix. */
function bodySentTo(fetchStub: ReturnType<typeof vi.fn>, path: string): Record<string, unknown> {
  const call = fetchStub.mock.calls.find(
    ([url]) => String(url).replace(/^https?:\/\/[^/]+/, "") === path,
  );
  return JSON.parse(String((call?.[1] as RequestInit).body));
}

/** Idea in, variants on screen. Suggest runs first on purpose: it fills `picked` and the asset
 *  slots, so anything this page leaks into the variants body leaks here. */
async function generateVariants(keepResponse?: Response) {
  const fetchStub = stubVariants(keepResponse);
  render(<Studio templates={library(statHeroSlots(9))} assets={LIBRARY} drafts={[]} />);
  typeIdea();
  fireEvent.click(screen.getByRole("button", { name: /suggest templates/i }));
  await screen.findByText(/stat-hero, because/);

  fireEvent.click(screen.getByRole("button", { name: /write variants/i }));
  await screen.findByRole("button", { name: /keep draft 13/i });
  return fetchStub;
}

describe("one idea, several drafts", () => {
  it("asks for the idea alone, even with the picker full", async () => {
    const fetchStub = await generateVariants();

    // Not `draftPayload`. The route varies the templates itself, so a hook_id riding along would
    // be ignored server-side — the request succeeds, three variants come back, and the page has
    // been lying about what it asked for. `count` is absent too, and stays absent now that
    // `/health` reports `variants_max`: the client reads the ceiling to *state* it, and the
    // server is still the only thing that applies it.
    expect(bodySentTo(fetchStub, "/drafts/variants")).toEqual({ idea: "a nine figure exit" });
  });

  it("renders them in the order they were written, not in id order", async () => {
    await generateVariants();

    expect(screen.getAllByRole("heading", { level: 3 }).map((h) => h.textContent)).toEqual([
      "Draft 13",
      "Draft 11",
      "Draft 12",
    ]);
  });

  it("shows each variant's own lineage, which is the whole point of comparing them", async () => {
    await generateVariants();

    // Three drafts of one idea differ only in the combination that wrote them; without this the
    // page is three blocks of text with no way to tell what is being chosen between.
    expect(screen.getByText("hook-13 v2")).toBeInTheDocument();
    expect(screen.getByText("structure-11 v1")).toBeInTheDocument();
    expect(screen.getByText("visual-12 v3")).toBeInTheDocument();
    // And each card names the assets in its picture, which is the other half of what is being
    // compared — the PNG is flattened, so this is the only place the logo in it is nameable.
    // Asserted here because the single-draft Lineage is a different call site: it was passed
    // the library while this one could have been passed `null` with nothing noticing.
    expect(screen.getAllByText("Pixii wordmark (#7)")).toHaveLength(3);
    expect(screen.getAllByText("Product shot (#9)")).toHaveLength(3);
  });

  it("reports what the batch spent, counted from the response", async () => {
    await generateVariants();

    // Read off `llm_calls`/`image_calls` and off `variants.length` — never assumed to be three.
    // The count is clamped server-side, so the number that arrived is the only true one.
    expect(screen.getByText(/3 chat completions and 3 image renders/)).toBeInTheDocument();
    expect(screen.getByText(/^3 drafts of one idea/)).toBeInTheDocument();
  });

  it("keeps the one that was clicked and discards exactly the others", async () => {
    const fetchStub = await generateVariants();

    fireEvent.click(screen.getByRole("button", { name: /keep draft 11/i }));

    await waitFor(() => expect(bodySentTo(fetchStub, "/drafts/variants/keep")).toBeTruthy());
    expect(bodySentTo(fetchStub, "/drafts/variants/keep")).toEqual({
      keep_id: 11,
      discard_ids: [13, 12],
    });
  });

  it("leaves the kept draft on screen, alone, and pushes its own id", async () => {
    const fetchStub = await generateVariants(jsonResponse(200, variant(11)));

    fireEvent.click(screen.getByRole("button", { name: /keep draft 11/i }));

    await waitFor(() =>
      expect(screen.queryByRole("button", { name: /keep draft 13/i })).not.toBeInTheDocument(),
    );
    expect(screen.getByText("hook-11 v2")).toBeInTheDocument();

    fireEvent.click(pushButton());
    await waitFor(() =>
      expect(fetchStub.mock.calls.map(([url]) => String(url))).toContainEqual(
        expect.stringMatching(/\/drafts\/11\/push$/),
      ),
    );
  });

  it("keeps every variant on screen when the keep is refused", async () => {
    // 409: a discard is already in Zernio, and the backend deleted nothing — not even the rows it
    // could have. Clearing the batch here would strand three drafts nobody can see into Inbox
    // queue 2, which is the exact orphan the route refuses to create.
    await generateVariants(
      jsonResponse(409, { detail: "draft(s) 13 are in Zernio and cannot be discarded" }),
    );

    fireEvent.click(screen.getByRole("button", { name: /keep draft 11/i }));

    await waitFor(() => expect(toastError).toHaveBeenCalled());
    expect(toastError.mock.calls[0][0]).toMatch(/cannot be discarded/);
    expect(screen.getByRole("button", { name: /keep draft 13/i })).toBeInTheDocument();
    expect(screen.getByText("Bundles are the anti-coupon.")).toBeInTheDocument();
  });

  it("replaces the batch with a single draft generated after it", async () => {
    // Both columns render into the same place, so a batch left standing over a freshly written
    // draft would hide a row that really exists — a write rendering as though it never happened.
    const fetchStub = await generateVariants();

    fireEvent.click(screen.getByRole("button", { name: /generate draft/i }));

    await waitFor(() => expect(screen.getByText(/A 9-figure exit/)).toBeInTheDocument());
    expect(screen.queryByRole("button", { name: /keep draft 13/i })).not.toBeInTheDocument();
    expect(bodySentTo(fetchStub, "/drafts")).toMatchObject({ hook_id: 1 });
  });
});

describe("a batch of a different size", () => {
  it("counts the drafts that arrived rather than the three it expected", async () => {
    // BATCH is three, and every number on that card is three — so a card hardcoding 3 passes
    // every assertion above. The count is clamped server-side (`settings.variants_max`) and
    // `variant_combinations` yields at most one per approved combination, so a batch smaller
    // than the ceiling is the ordinary case rather than a contrived one.
    const two = { variants: [variant(21), variant(22)], llm_calls: 2, image_calls: 2 };
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string) => {
        const path = String(url).replace(/^https?:\/\/[^/]+/, "");
        if (path === "/drafts/variants") return Promise.resolve(jsonResponse(201, two));
        return Promise.resolve(jsonResponse(201, DRAFT));
      }),
    );
    render(<Studio templates={library(statHeroSlots(9))} assets={LIBRARY} drafts={[]} />);
    typeIdea();

    fireEvent.click(screen.getByRole("button", { name: /write variants/i }));

    await screen.findByRole("button", { name: /keep draft 21/i });
    expect(screen.getByText(/^2 drafts of one idea/)).toBeInTheDocument();
    expect(screen.getByText(/2 chat completions and 2 image renders/)).toBeInTheDocument();
  });
});

/* The page itself, because `?draft=` is read there and the guard that keeps a malformed param
 * from becoming a request lives there too. Rendered the way `posts/[id]/page.test.tsx` renders
 * its detail page: the server component is an async function, so it is awaited and its output
 * handed to RTL. */
function stubPage(drafts: unknown[], draftResponse?: Response, health?: Response) {
  const fetchStub = vi.fn((url: string) => {
    const path = String(url).replace(/^https?:\/\/[^/]+/, "");
    if (path === "/templates") return Promise.resolve(jsonResponse(200, library([])));
    if (path === "/assets") return Promise.resolve(jsonResponse(200, LIBRARY));
    if (path === "/drafts") return Promise.resolve(jsonResponse(200, drafts));
    // Its own branch, and it has to sit ABOVE the `draftResponse` fallthrough: that clause
    // answers anything it has not already matched, so without this line `/health` would be
    // handed a `DraftOut`, `variants_max` would arrive `undefined`, and the control would
    // render nonsense while every test here still passed.
    if (path === "/health") return Promise.resolve(health ?? jsonResponse(200, HEALTH));
    if (draftResponse) return Promise.resolve(draftResponse);
    return Promise.reject(new Error(`unexpected request: ${path}`));
  });
  vi.stubGlobal("fetch", fetchStub);
  return fetchStub;
}

/** `GET /health`. `variants_max` is deliberately NOT 3 — 3 is the setting's default and the
 *  number the page used to hardcode, so a fixture on 3 would pass against the very bug this
 *  slice removes. It is a sibling of `credentials`, never a key inside it: the Inbox footer
 *  renders that object row-per-key as health lights, and a number in there draws a junk one. */
const HEALTH = {
  status: "ok",
  database: true,
  credentials: { zernio: true },
  variants_max: 7,
};

describe("the ?draft= parameter", () => {
  it("loads the draft the url names", async () => {
    const fetchStub = stubPage([], jsonResponse(200, LOADED));

    render(await StudioPage({ searchParams: Promise.resolve({ draft: "424" }) }));

    expect(fetchStub.mock.calls.map(([url]) => String(url))).toContainEqual(
      expect.stringMatching(/\/drafts\/424$/),
    );
    expect(screen.getByText(/Bundles are the anti-coupon/)).toBeInTheDocument();
    expect(screen.queryByText("No draft yet.")).not.toBeInTheDocument();
  });

  it("reports an unknown id in the API's own words", async () => {
    const fetchStub = stubPage([], jsonResponse(404, { detail: "no draft 999" }));

    render(await StudioPage({ searchParams: Promise.resolve({ draft: "999" }) }));

    expect(fetchStub.mock.calls.map(([url]) => String(url))).toContainEqual(
      expect.stringMatching(/\/drafts\/999$/),
    );
    expect(screen.getByRole("alert")).toHaveTextContent("no draft 999");
    expect(screen.queryByText("No draft yet.")).not.toBeInTheDocument();
  });

  it("refuses a non-numeric id without asking the API about it", async () => {
    // The guard, proved by the request that was never made — otherwise the message below could
    // be a 422 the backend happened to phrase well, and `?draft=` on its own would still go out
    // as a request for `/drafts/`.
    const fetchStub = stubPage([]);

    render(await StudioPage({ searchParams: Promise.resolve({ draft: "abc" }) }));

    expect(fetchStub.mock.calls.map(([url]) => String(url))).not.toContainEqual(
      expect.stringContaining("/drafts/"),
    );
    expect(screen.getByRole("alert")).toHaveTextContent(/"abc" is not a draft id/);
    expect(screen.queryByText("No draft yet.")).not.toBeInTheDocument();
  });

  it("shows the fresh-session empty state when nothing was asked for", async () => {
    // The other side of every negative assertion above: without this, "the empty state is not
    // rendered" would pass on a page that had stopped rendering it at all.
    stubPage([]);

    render(await StudioPage({ searchParams: Promise.resolve({}) }));

    expect(screen.getByText("No draft yet.")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("lists the drafts it was given", async () => {
    stubPage([{ ...LOADED, visual_png: "iVBORw0KGgoAAAANSUhEUg" }]);

    render(await StudioPage({ searchParams: Promise.resolve({}) }));

    expect(screen.getByRole("link", { name: /Amazon bundles/ })).toHaveAttribute(
      "href",
      "/studio?draft=424",
    );
    /* The `expect(container.innerHTML).not.toContain(base64)` that used to sit here is gone.
       It was unfalsifiable: the list renders three fields and never a picture, so the PNG is
       absent from the DOM whether or not `page.tsx` narrows the row. Replacing the map with
       `{ ...d }` left it green. What the narrowing actually saves is the RSC payload, which no
       jsdom render produces — asserted where it is observable, on the props crossing the
       boundary, in `page.narrowing.test.tsx`. */
  });

  it("shows the draft the url now names after navigating from another one", async () => {
    // `Studio` seeds its state from `initialDraft` through `useState`, which never re-seeds
    // from a changed prop, so a client-side navigation from ?draft=424 to ?draft=555 would
    // reconcile onto the same instance and go on showing draft 424. The `key` is what makes
    // it a remount. Two renders of the page in the same tree is what a navigation looks like.
    stubPage([], jsonResponse(200, LOADED));
    const { rerender } = render(await StudioPage({ searchParams: Promise.resolve({ draft: "424" }) }));
    expect(screen.getByText(/Bundles are the anti-coupon/)).toBeInTheDocument();

    stubPage([], jsonResponse(200, { ...LOADED, id: 555, full_text: "A different draft entirely." }));
    rerender(await StudioPage({ searchParams: Promise.resolve({ draft: "555" }) }));

    expect(screen.getByText("A different draft entirely.")).toBeInTheDocument();
    expect(screen.queryByText(/Bundles are the anti-coupon/)).not.toBeInTheDocument();
  });
});

/* US-019. A control that spends money has to say what it will spend *before* the press. The
   response already reports `llm_calls`/`image_calls` after the fact, and that is a different
   statement: it is the receipt, not the price.
 *
 * The ceiling belongs to the server — `settings.variants_max`, applied in `api_drafts.py` — so
 * the number is read off `/health` and never written here. These run through `StudioPage`
 * rather than through `Studio` directly because the wire is the thing being defended: the prop
 * arrives from a fetch that a component-level render would skip entirely. */
describe("what a variants run will spend", () => {
  it("names the server's ceiling, in the same words the receipt uses", async () => {
    stubPage([]);

    render(await StudioPage({ searchParams: Promise.resolve({}) }));

    // 7, because that is what this server said. A page that hardcodes 3 — which is what it did
    // before this slice, and what `variants_max` defaults to — fails here.
    //
    // The whole sentence, not just the `calls()` half: the count and the two spend figures are
    // three separate reads of the same prop, and asserting one of them let a mutation that
    // hardcoded the other two survive. Measured, not assumed — it survived exactly that.
    expect(
      screen.getByText(/up to 7 of them — at most 7 chat completions and 7 image renders/),
    ).toBeInTheDocument();
  });

  it("says nothing about the count when /health could not be read", async () => {
    // Never a fallback of 3. A wrong number stated confidently is worse than no number, and 3
    // is exactly the number this slice exists to stop asserting. The helper text keeps its
    // per-variant sentence, which is true whatever the ceiling is.
    stubPage([], undefined, jsonResponse(503, { detail: "database connection refused" }));

    render(await StudioPage({ searchParams: Promise.resolve({}) }));

    expect(screen.getByText(/spends a chat completion and a render per variant/)).toBeInTheDocument();
    expect(screen.queryByText(/at most/)).not.toBeInTheDocument();
    // And a dead `/health` does not take the page down with it — the queues-vs-footer split the
    // Inbox already makes. The button is still there and the rest of the page still renders.
    expect(screen.getByRole("button", { name: /write variants/i })).toBeInTheDocument();
    expect(screen.getByText("No draft yet.")).toBeInTheDocument();
  });
});

describe("the fresh-session empty state", () => {
  it("is not tinted like a failure", () => {
    // It is the correct state on every visit to a fresh Studio, and it sat on `bg-surface-2`
    // — the raised brand cream, the same warm panel the amber notices on this page sit beside.
    // The three failure messages in this column are the ones that get to look like failures.
    render(<Studio templates={library([])} assets={LIBRARY} drafts={[]} />);

    const card = screen.getByText("No draft yet.").closest("div");
    expect(card).not.toHaveClass("bg-surface-2");
    // The card itself survives — an assertion about a missing class passes trivially on a
    // deleted element.
    expect(card).toHaveClass("border-border");
  });
});
