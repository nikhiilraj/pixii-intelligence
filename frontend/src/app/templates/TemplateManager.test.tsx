import "@testing-library/jest-dom/vitest";

import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Template } from "@/lib/api";

import TemplateManager, { extractPath } from "./TemplateManager";

/* This is the page where 50 proposals are approved or retired, and it is also where the two
 * extract buttons live — the ones that wrote 13 template rows into the live database when an
 * agent clicked one. So: every request is stubbed, and the stub REJECTS any path this page is
 * not supposed to reach. A typo'd URL or a forgotten stub is a test failure here rather than a
 * live POST, which is the only mechanical guarantee against repeating that.
 *
 * What is asserted is the request that leaves — its path, its method and its body — never that a
 * button with the right word on it exists. "approve" pointing at `/retire` renders identically.
 *
 * What is NOT asserted, deliberately: anything Radix owns. Opening either Select, arrowing
 * through it, typeahead, Escape. Those are upstream-tested and jsdom only approximates focus.
 *
 * That has a consequence which silently weakens assertions, so it is worth naming: while a
 * Select is closed Radix keeps `SelectContent` in a detached DocumentFragment, so
 * `queryByText("from our own posts")` is `null` whether the control works or not. Every
 * assertion about a Select below is made on its TRIGGER — which is really in the document and
 * renders the selected item's text even while the list is closed — or on the request the page
 * then sends. None is made on an item label. */

const toastError = vi.hoisted(() => vi.fn());
const refresh = vi.hoisted(() => vi.fn());
vi.mock("sonner", () => ({ toast: { error: toastError, success: vi.fn() } }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh }) }));

function template(overrides: Partial<Template> = {}): Template {
  return {
    id: 3,
    family_id: "fam-hook",
    version: 1,
    kind: "hook",
    name: "transformation",
    status: "proposed",
    body: { pattern: "{value} turned into {outcome}", tone: "direct" },
    slots: [],
    provenance: [],
    notes: "",
    ...overrides,
  };
}

/** One of each state the row's controls branch on. */
const PROPOSED = template();
const APPROVED = template({ id: 4, kind: "structure", name: "case-loop", status: "approved" });
const RETIRED = template({ id: 5, kind: "hook", name: "old-hook", status: "retired" });
/** The one used for the edit tests: a visual, so the kind it restores on cancel is not the
 *  default, and a body that is NOT the blank visual body so the restore is provable. */
const VISUAL = template({
  id: 6,
  kind: "visual",
  name: "quote-card",
  status: "approved",
  version: 2,
  body: { renderer: "html", component: "quote_card" },
});

const LIBRARY = [PROPOSED, APPROVED, RETIRED, VISUAL];

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

/** Every path this page is allowed to reach. `/templates/{id}/preview` is deliberately absent:
 *  it costs a real image render, it is out of this slice, and a stray click on it must fail the
 *  test rather than quietly succeed. */
const ALLOWED = [
  /^\/templates$/,
  /^\/templates\/\d+$/,
  /^\/templates\/\d+\/(approve|retire)$/,
  /^\/templates\/extract\/(hooks|structures|visuals)\?/,
];

function stubApi(response: Response = jsonResponse(200, { ok: true })) {
  const fetchStub = vi.fn((url: string) => {
    const path = String(url).replace(/^https?:\/\/[^/]+/, "");
    if (!ALLOWED.some((allowed) => allowed.test(path)))
      return Promise.reject(new Error(`unexpected request: ${path}`));
    return Promise.resolve(response.clone());
  });
  vi.stubGlobal("fetch", fetchStub);
  return fetchStub;
}

/** Every request the page made, base stripped — asserted as a whole list, so a second stray
 *  request is a failure too. */
function requests(fetchStub: ReturnType<typeof vi.fn>) {
  return fetchStub.mock.calls.map(([url, init]) => {
    const { method, body } = (init ?? {}) as RequestInit;
    return {
      path: String(url).replace(/^https?:\/\/[^/]+/, ""),
      method,
      body: body === undefined ? undefined : JSON.parse(String(body)),
    };
  });
}

/** The controls belonging to one row, found by the name it renders. */
function row(name: string) {
  return within(screen.getByText(name).closest("li")!);
}

const nameField = () => screen.getByPlaceholderText("name");
/** The one textarea on the page. `getByRole("textbox")` matches the name input too. */
const bodyField = () => document.querySelector("textarea")!;
/** The one `<form>` on the page — the authoring/edit form. Needed to scope a `getByRole`
 *  query below: a visual ROW in the list also renders a "preview" link with the same
 *  accessible name, so an unscoped query on that name is ambiguous the moment a visual
 *  is in `LIBRARY`, which every test in this file renders. */
const formEl = () => document.querySelector("form")!;

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  toastError.mockReset();
  refresh.mockReset();
});

describe("approving and retiring", () => {
  it("approves the row that was clicked, at that row's own url", async () => {
    // A label-only test passes on a page that posts to the wrong id or the wrong verb, and both
    // are one-way: approving makes a template usable by generation, retiring takes it out.
    const fetchStub = stubApi();
    render(<TemplateManager initial={LIBRARY} />);

    fireEvent.click(row("transformation").getByRole("button", { name: "approve" }));

    await waitFor(() => expect(fetchStub).toHaveBeenCalled());
    expect(requests(fetchStub)).toEqual([
      { path: "/templates/3/approve", method: "POST", body: undefined },
    ]);
  });

  it("retires the row that was clicked, at that row's own url", async () => {
    const fetchStub = stubApi();
    render(<TemplateManager initial={LIBRARY} />);

    fireEvent.click(row("case-loop").getByRole("button", { name: "retire" }));

    await waitFor(() => expect(fetchStub).toHaveBeenCalled());
    expect(requests(fetchStub)).toEqual([
      { path: "/templates/4/retire", method: "POST", body: undefined },
    ]);
  });

  it("re-reads the list after a mutation lands", async () => {
    // The row's new status comes off the server, not off local state — without the refresh the
    // approved template keeps rendering as "proposed" and gets approved twice.
    const fetchStub = stubApi();
    render(<TemplateManager initial={LIBRARY} />);

    fireEvent.click(row("transformation").getByRole("button", { name: "approve" }));

    await waitFor(() => expect(refresh).toHaveBeenCalledTimes(1));
    expect(fetchStub).toHaveBeenCalledTimes(1);
  });

  it("does not re-read the list when the mutation was refused", async () => {
    // A refresh on a failure would redraw the row unchanged and read as "nothing happened",
    // beside a toast saying something did.
    stubApi(jsonResponse(409, { detail: "template 3 is already approved" }));
    render(<TemplateManager initial={LIBRARY} />);

    fireEvent.click(row("transformation").getByRole("button", { name: "approve" }));

    await waitFor(() => expect(toastError).toHaveBeenCalledWith("template 3 is already approved"));
    expect(refresh).not.toHaveBeenCalled();
  });

  it("offers approve only on a proposal, and no verb at all on a retired row", () => {
    // Real absences: these are plain `<button>`s in the row, not portalled content, so a null
    // query here means the button is genuinely not rendered. Each negative is paired with a
    // positive on the same row, so none of them can pass on a row that failed to render.
    render(<TemplateManager initial={LIBRARY} />);

    expect(row("case-loop").queryByRole("button", { name: "approve" })).toBeNull();
    expect(row("case-loop").getByRole("button", { name: "retire" })).toBeInTheDocument();

    expect(row("old-hook").queryByRole("button", { name: "retire" })).toBeNull();
    expect(row("old-hook").queryByRole("button", { name: "edit" })).toBeNull();
    expect(within(screen.getByText("old-hook").closest("li")!).getByText("retired")).toBeVisible();

    // Preview renders a template, so it belongs only to the kind that has one to render. The
    // stub above rejects `/preview` outright, so an offer on a hook is a click away from a
    // failed request — and it went untested: making the button unconditional stayed green.
    expect(row("quote-card").getByRole("button", { name: "preview" })).toBeInTheDocument();
    expect(row("transformation").queryByRole("button", { name: "preview" })).toBeNull();
    expect(row("case-loop").queryByRole("button", { name: "preview" })).toBeNull();
  });
});

/* Whose posts a template was read from, which is the difference a reviewer is judging on: a
   shape proven in our own writing against one borrowed from a creator. Three states and three
   renderings — and the third is the one that has to exist, because most of the queue predates
   the field and a blank beside rows marked "borrowed" reads as "not borrowed", a claim the row
   does not carry. Nothing asserted this: both dropping the unrecorded branch and stringifying
   a missing cohort left the suite green. */
describe("the cohort a template was read from", () => {
  it("names each of the three states, and never invents one", () => {
    render(
      <TemplateManager
        initial={[
          template({ id: 10, name: "ours", body: { cohort: "voice" } }),
          template({ id: 11, name: "borrowed", body: { cohort: "inspiration" } }),
          template({ id: 12, name: "older-than-the-field", body: { pattern: "x" } }),
        ]}
      />,
    );

    expect(row("ours").getByText("proven in our own posts")).toBeInTheDocument();
    expect(row("borrowed").getByText("borrowed from a creator")).toBeInTheDocument();
    expect(row("older-than-the-field").getByText("cohort unrecorded")).toBeInTheDocument();
    // And the unrecorded row does not quietly borrow either label.
    expect(row("older-than-the-field").queryByText(/proven|borrowed from/)).toBeNull();
  });
});

/* How many corpus posts a template covers — the number the one-time validation read works
   from, and the number that now decides whether a proposal is worth approving. Reading it
   used to mean writing SQL.

   The empty case is the one with teeth. Every hand-authored template has an empty
   `provenance`, as do the 15 structures currently in the library, and that is an absence of
   a record rather than a measurement of zero: printing `0` would claim extraction looked and
   found nothing. Before this, the line was rendered only when `provenance.length > 0`, so a
   row covering nothing and a row whose coverage was never recorded were the same blank. */
describe("the coverage count on a row", () => {
  it("counts the posts it was read from, and prints a dash where nothing was recorded", () => {
    render(
      <TemplateManager
        initial={[
          template({ id: 20, name: "read-from-three", provenance: ["p1", "p2", "p3"] }),
          template({ id: 21, name: "read-from-one", provenance: ["p1"] }),
          template({ id: 22, name: "hand-authored", provenance: [] }),
        ]}
      />,
    );

    expect(row("read-from-three").getByText(/^coverage/)).toHaveTextContent("coverage 3 posts");
    // Singular, because "1 posts" beside a count that decides approval reads as a rounding.
    expect(row("read-from-one").getByText(/^coverage/)).toHaveTextContent("coverage 1 post");
    expect(row("hand-authored").getByText(/^coverage/)).toHaveTextContent("coverage —");
    // The anti-zero assertion, stated separately: it is the whole point of the dash and the
    // one mutation — `: 0` in place of the dash — that leaves the page looking normal.
    expect(row("hand-authored").queryByText(/coverage 0/)).toBeNull();
  });
});

describe("the extraction cohort reaching the query string", () => {
  it("sends the chosen cohort in the hooks query", async () => {
    const fetchStub = stubApi();
    render(<TemplateManager initial={LIBRARY} />);

    fireEvent.click(screen.getByRole("button", { name: /extract hooks/i }));

    await waitFor(() => expect(fetchStub).toHaveBeenCalled());
    // Full equality, not `toContain`. The day this select grows an "any cohort" option, its
    // sentinel reaches exactly here — and a filter that silently stops filtering leaves the page
    // rendering normally, which is the shape that has already cost this codebase once.
    expect(requests(fetchStub)).toEqual([
      { path: "/templates/extract/hooks?cohort=voice", method: "POST", body: undefined },
    ]);
  });

  it("sends the cohort in the structures query, and nothing the route no longer reads", async () => {
    const fetchStub = stubApi();
    render(<TemplateManager initial={LIBRARY} />);

    fireEvent.click(screen.getByRole("button", { name: /extract structures/i }));

    await waitFor(() => expect(fetchStub).toHaveBeenCalled());
    // The exact string, so a re-added `sample_size` fails here: FastAPI ignores an unknown
    // query parameter, so nothing else on either side of the wire would notice one.
    expect(requests(fetchStub)).toEqual([
      {
        path: "/templates/extract/structures?cohort=voice",
        method: "POST",
        body: undefined,
      },
    ]);
  });

  it("sends the cohort when extracting visual layouts", async () => {
    const fetchStub = stubApi();
    render(<TemplateManager initial={LIBRARY} />);

    fireEvent.click(screen.getByRole("button", { name: /extract visual layouts/i }));

    await waitFor(() => expect(fetchStub).toHaveBeenCalled());
    expect(requests(fetchStub)).toEqual([
      { path: "/templates/extract/visuals?cohort=voice", method: "POST", body: undefined },
    ]);
  });

  it("carries either cohort into either endpoint's query", () => {
    // The component tests above can only prove the default: `cohort` is set by a Radix trigger,
    // which is a button that cannot be driven in jsdom. So the mapping from a chosen cohort to
    // the sent query is asserted on `extractPath` — the function BOTH buttons call, with no
    // second copy of the query-building inline for a mutation to hide in.
    expect(extractPath("hooks", "voice")).toBe("/templates/extract/hooks?cohort=voice");
    expect(extractPath("hooks", "inspiration")).toBe("/templates/extract/hooks?cohort=inspiration");
    expect(extractPath("structures", "inspiration")).toBe(
      "/templates/extract/structures?cohort=inspiration",
    );
    expect(extractPath("visuals", "inspiration")).toBe(
      "/templates/extract/visuals?cohort=inspiration",
    );
  });

  it("starts on the cohort it says it is on", () => {
    // Asserted on the trigger, which is really in the document and renders the selected item's
    // text — never on the item label, which lives in a detached fragment while the list is
    // closed and would answer `null` on a control that does not work at all.
    render(<TemplateManager initial={LIBRARY} />);

    expect(screen.getByLabelText("extraction cohort")).toHaveTextContent("from our own posts");
  });
});

describe("authoring a template by hand", () => {
  it("posts the kind, the name and the body parsed out of the textarea", async () => {
    const fetchStub = stubApi(jsonResponse(201, template()));
    render(<TemplateManager initial={LIBRARY} />);

    fireEvent.change(nameField(), { target: { value: "curiosity-gap" } });
    fireEvent.change(bodyField(), { target: { value: '{"pattern":"what if {x}"}' } });
    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => expect(fetchStub).toHaveBeenCalled());
    // The body goes as an object, not as the string that was typed — the column is JSONB and a
    // string in it is accepted, stored and unreadable by every consumer.
    expect(requests(fetchStub)).toEqual([
      {
        path: "/templates",
        method: "POST",
        body: { kind: "hook", name: "curiosity-gap", body: { pattern: "what if {x}" } },
      },
    ]);
  });

  it("refuses to send a body that is not JSON", async () => {
    // The one client-side failure on this page. It has to stop the request: the API would take
    // the malformed string as a 422, but the point is that nothing leaves at all.
    const fetchStub = stubApi();
    render(<TemplateManager initial={LIBRARY} />);

    fireEvent.change(nameField(), { target: { value: "broken" } });
    fireEvent.change(bodyField(), { target: { value: "{not json" } });
    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() => expect(toastError).toHaveBeenCalledWith("Body is not valid JSON."));
    expect(fetchStub).not.toHaveBeenCalled();
  });

  it("keeps what was typed when the API refuses it", async () => {
    // A form that clears itself on a 409 has thrown away work the human did and the reason it
    // was refused in the same instant.
    stubApi(jsonResponse(409, { detail: "a template named curiosity-gap already exists" }));
    render(<TemplateManager initial={LIBRARY} />);

    fireEvent.change(nameField(), { target: { value: "curiosity-gap" } });
    fireEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() =>
      expect(toastError).toHaveBeenCalledWith("a template named curiosity-gap already exists"),
    );
    expect(nameField()).toHaveValue("curiosity-gap");
  });
});

describe("editing writes a new version", () => {
  /** Open the edit form on the visual row — the whole premise of the page is that this PUTs a
   *  new version rather than overwriting v2. */
  function startEdit() {
    fireEvent.click(row("quote-card").getByRole("button", { name: "edit" }));
  }

  it("PUTs to the row's own url, and says which version it is writing", async () => {
    const fetchStub = stubApi(jsonResponse(200, VISUAL));
    render(<TemplateManager initial={LIBRARY} />);

    startEdit();
    // The heading is the only thing on screen telling the human this is a new version and not an
    // overwrite. v2 is being revised, so v3 is what lands.
    expect(screen.getByRole("heading", { level: 2 })).toHaveTextContent('Revise "quote-card" → v3');
    // The form arrives holding the row's current wording, which is what "revise" means.
    expect(nameField()).toHaveValue("quote-card");
    expect(bodyField()).toHaveValue(JSON.stringify(VISUAL.body, null, 2));
    // And the kind select is not offered while revising: the PUT below carries no `kind`, so a
    // control appearing to change it would change nothing. A Select TRIGGER is a real element in
    // the document rather than portalled content, so this absence is a real absence — and the
    // next test proves the same trigger is present when authoring, so it cannot pass vacuously.
    expect(screen.queryByLabelText("template kind")).toBeNull();

    fireEvent.change(bodyField(), { target: { value: '{"renderer":"html","component":"pull"}' } });
    fireEvent.click(screen.getByRole("button", { name: "Save as new version" }));

    await waitFor(() => expect(fetchStub).toHaveBeenCalled());
    // PUT, not POST, and no `kind`: the version inherits its family's kind, and a POST here
    // would start a new family — performance detaching from the wording that earned it, which is
    // the one thing this page exists to prevent.
    expect(requests(fetchStub)).toEqual([
      {
        path: "/templates/6",
        method: "PUT",
        body: { name: "quote-card", body: { renderer: "html", component: "pull" } },
      },
    ]);
  });

  it("goes back to authoring once the version is written", async () => {
    const fetchStub = stubApi(jsonResponse(200, VISUAL));
    render(<TemplateManager initial={LIBRARY} />);

    startEdit();
    fireEvent.click(screen.getByRole("button", { name: "Save as new version" }));

    await waitFor(() => expect(fetchStub).toHaveBeenCalled());
    // A form still saying "Revise" after the PUT landed would send the next save at the row that
    // was just superseded. The kind select is hidden while editing and back afterwards — and it
    // is a trigger, in the document, so both directions of this are real.
    await waitFor(() => expect(screen.getByRole("heading", { level: 2 })).toHaveTextContent(
      "Author a template",
    ));
    expect(screen.getByLabelText("template kind")).toBeInTheDocument();
    expect(nameField()).toHaveValue("");
  });

  it("stays on the edited row's kind when the edit is cancelled", async () => {
    // The kind→blank-body swap, driven through the one real path that reaches it in jsdom:
    // `startEdit` sets the kind off the row, and `reset` fills the textarea from that kind. A
    // `reset` hardcoded to "hook" — or a wrong entry in the blank-body table — fails here.
    //
    // The Select's own `onValueChange` is the other caller of that same table and is NOT
    // exercised: driving it means opening a Radix listbox, which jsdom cannot do. This is the
    // ceiling, stated rather than faked with a query into a closed portal.
    stubApi();
    render(<TemplateManager initial={LIBRARY} />);

    startEdit();
    fireEvent.click(screen.getByRole("button", { name: "cancel" }));

    expect(screen.getByLabelText("template kind")).toHaveTextContent("visual");
    expect(bodyField()).toHaveValue(
      JSON.stringify({ renderer: "html", component: "stat_hero" }, null, 2),
    );
  });
});

describe("the preview pane in the editor", () => {
  it("offers a preview for a visual and not for a hook", () => {
    // The brief for this test reached for `userEvent.selectOptions` on the kind control, but
    // that control is a Radix `Select`, not a native `<select>` — and this file's own header
    // comment already states the ceiling: driving a Radix listbox open is not something jsdom
    // can do, so no query into it can prove the control works. `startEdit` is the one real path
    // into kind="visual" this file exercises anywhere (see "stays on the edited row's kind..."
    // below), and it goes through the same `kind` state the Select would set.
    render(<TemplateManager initial={LIBRARY} />);

    // Scoped to the form: `row("quote-card")` already renders its own "preview" link in the
    // list (asserted in "offers approve only on a proposal..." above), with the same
    // accessible name, so an unscoped query is ambiguous the moment a visual is in `LIBRARY`.
    expect(within(formEl()).queryByRole("button", { name: /^preview$/i })).not.toBeInTheDocument();

    fireEvent.click(row("quote-card").getByRole("button", { name: "edit" }));

    expect(within(formEl()).getByRole("button", { name: /^preview$/i })).toBeInTheDocument();
  });
});

describe("an empty library", () => {
  it("says the library is empty and names the two ways into it", () => {
    // `page.tsx` renders ApiFailureNotice instead of this component when the read fails, so `[]`
    // here really is empty and saying so is not the error-as-empty-state bug.
    render(<TemplateManager initial={[]} />);

    expect(screen.getByText("No template exists yet.")).toBeInTheDocument();
    // The extract buttons are still the way out of it.
    expect(screen.getByRole("button", { name: /extract hooks/i })).toBeInTheDocument();
  });
});
