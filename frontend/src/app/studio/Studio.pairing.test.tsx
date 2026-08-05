import "@testing-library/jest-dom/vitest";

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Draft, Template } from "@/lib/api";

import Studio, { hookGroups, pairKey } from "./Studio";

/* The structure/hook pairing, which extraction has always recorded and this picker always threw
 * away. `GET /templates/{id}/compatible-hooks` resolves `compatible_hook_families` to the hooks
 * that are still approved; the picker groups its hook list by it.
 *
 * Three properties are worth pinning and only the last is about rendering:
 *
 * - **grouping never traps the operator.** Every approved hook is offered in every state, so a
 *   structure whose pairings are empty, stale or unreadable cannot leave the list with one
 *   option out of six — which is what the live library would do under a filter, today;
 * - **failure and emptiness are different states**, and neither reads as "no hook is
 *   compatible";
 * - the pairing is read off `(family_id, version)` and never off the name, because the live
 *   library holds three pairs of hooks that share a name across different families.
 *
 * What is NOT tested here, for the reason `Studio.test.tsx`'s header gives: the group headings
 * themselves. They render inside `SelectContent`, which Radix keeps in a detached fragment
 * while the Select is closed, and a Radix trigger cannot be opened in jsdom — so `queryByText`
 * for a heading is `null` whether the grouping works or not, and asserting on it would be an
 * assertion that passes against a picker with no grouping in it at all. The headings are
 * asserted where they are decided, in `hookGroups`; what the component tests is the sentence
 * beside the control, which is outside the portal and is the only account of the grouping a
 * reader who never opens the dropdown gets.
 *
 * The structure is chosen through Suggest for the same reason the depth is raised through the
 * refusal button in `Studio.depth.test.tsx`: it is the one path that sets `picked.structure`
 * without opening a listbox, and it is a path an operator actually takes. */

vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn() } }));

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function hook(id: number, name: string, family: string, version = 1): Template {
  return {
    id,
    family_id: family,
    version,
    kind: "hook",
    name,
    status: "approved",
    body: {},
    slots: [],
    provenance: [],
    notes: "",
  };
}

const EQUATION = hook(1, "ai-time-value-equation", "fam-equation");
const QUESTION = hook(2, "open-question-then-tested", "fam-question");
const RECURRING = hook(3, "small-input-big-recurring-result", "fam-recurring");
const HOOKS = [EQUATION, QUESTION, RECURRING];

describe("hookGroups", () => {
  it("offers every approved hook whether or not a pairing was read", () => {
    /* The never-trap property, stated as an invariant rather than as three examples: whatever
     * the pairing is, the groups are a permutation of the hooks handed in. A filter is exactly
     * the change that breaks this, which is what makes it the assertion worth having. */
    const states: (Template[] | null)[] = [
      null,
      [],
      [QUESTION],
      HOOKS,
      [hook(99, "retired-elsewhere", "fam-nobody")],
    ];

    for (const pairing of states) {
      const offered = hookGroups(HOOKS, pairing, "case-study-loop").flatMap((g) => g.hooks);
      expect([...offered].sort((a, b) => a.id - b.id)).toEqual(HOOKS);
    }
  });

  it("groups the recorded pairing first and leaves the rest under their own heading", () => {
    const groups = hookGroups(HOOKS, [QUESTION], "case-study-loop");

    expect(groups.map((g) => g.label)).toEqual([
      "Recorded as pairing with case-study-loop",
      "Every other approved hook",
    ]);
    expect(groups[0].hooks).toEqual([QUESTION]);
    expect(groups[1].hooks).toEqual([EQUATION, RECURRING]);
  });

  it("does not offer a hook the route named that this page does not hold", () => {
    /* Partitioned, never concatenated. A row the route resolved against a library this page has
     * not re-read would otherwise become an option whose id is sent for a template the page
     * cannot render or name in the Lineage block. */
    const stranger = hook(99, "not-in-the-picker", "fam-stranger");

    const offered = hookGroups(HOOKS, [QUESTION, stranger], "case-study-loop").flatMap(
      (g) => g.hooks,
    );

    expect(offered).not.toContainEqual(stranger);
    expect(offered).toHaveLength(3);
  });

  it("reads the pairing off the family and version, never off the name", () => {
    /* The live library holds `small-input-big-recurring-result` twice — family `7073134a…`
     * approved and family `b8b3ec31…` proposed — plus two more name collisions. Matching by
     * name would group a family the structure never recorded, silently. */
    const namesake = hook(4, "small-input-big-recurring-result", "fam-other-recurring");
    const both = [RECURRING, namesake];

    const groups = hookGroups(both, [RECURRING], "case-study-loop");

    expect(groups[0].hooks).toEqual([RECURRING]);
    expect(groups[1].hooks).toEqual([namesake]);
    expect(pairKey(RECURRING)).not.toEqual(pairKey(namesake));
  });

  it("says nothing that ranks", () => {
    /* The project never ranks, and a heading is where that would leak in first. Compatibility
     * is a structural fact extraction recorded, not a claim that a hook performs better. */
    const labels = hookGroups(HOOKS, [QUESTION], "case-study-loop")
      .map((g) => g.label ?? "")
      .join(" ")
      .toLowerCase();

    for (const word of ["best", "top", "recommend", "better", "strongest", "preferred"]) {
      expect(labels).not.toContain(word);
    }
  });

  it("leaves the list ungrouped when nothing in it was recorded", () => {
    expect(hookGroups(HOOKS, [], "case-study-loop")).toEqual([{ label: null, hooks: HOOKS }]);
  });
});

const STRUCTURE: Template = {
  id: 10,
  family_id: "fam-structure",
  version: 1,
  kind: "structure",
  name: "case-study-loop",
  status: "approved",
  body: {},
  slots: [],
  provenance: [],
  notes: "",
};

const VISUAL: Template = { ...STRUCTURE, id: 20, family_id: "fam-visual", kind: "visual", name: "stat-card" };

const SUGGESTION = {
  hook: { id: EQUATION.id },
  structure: { id: STRUCTURE.id },
  visual: { id: VISUAL.id },
  reason: "picked for the shape of the idea",
};

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

/** Enough of a `Draft` for the right column to render one. `POST /drafts/workflow` answers with
 *  a real row at `planning`; the assertion in the last test is about what was *sent*. */
const DRAFT: Draft = {
  id: 7,
  idea: "What one change did to a checkout funnel",
  mode: "directed",
  hook_text: "",
  body_text: "",
  full_text: "",
  visual_values: {},
  asset_values: {},
  visual_error: null,
  visual_png: null,
  has_previous_visual: false,
  zernio_post_id: null,
  revision: 1,
  pushed_revision: null,
  lineage: { hook: null, structure: null, visual: null },
  generation_stage: "planning",
  generation_error: null,
  gate_results: [],
  readiness_result: null,
  verification_result: null,
  revision_rounds: 0,
  editorial: null,
};

/** Routes by path rather than by call order: the pairing read is fired by an effect and the
 *  suggestion by a click, so a queue would pin an ordering neither of them promises. */
function stub(pairing: () => Promise<Response>) {
  const fetchStub = vi.fn((url: string, _init?: RequestInit) => {
    if (String(url).includes("/compatible-hooks")) return pairing();
    if (String(url).includes("/drafts/workflow")) return Promise.resolve(json(200, DRAFT));
    return Promise.resolve(json(200, SUGGESTION));
  });
  vi.stubGlobal("fetch", fetchStub);
  return fetchStub;
}

/** Studio with an idea typed and Suggest pressed — the one path that chooses a structure
 *  without a Radix trigger. */
function suggest() {
  render(
    <Studio
      assets={[]}
      drafts={[]}
      templates={[...HOOKS, STRUCTURE, VISUAL]}
    />,
  );
  fireEvent.change(screen.getByPlaceholderText(/what is this post about/i), {
    target: { value: "What one change did to a checkout funnel" },
  });
  fireEvent.click(screen.getByRole("button", { name: /suggest templates/i }));
}

describe("the pairing beside the picker", () => {
  it("reports a failed read as a failed read, and not as an empty pairing", async () => {
    /* The distinction `ApiResult` exists for. "This structure records no pairing" on a 500
     * would be the page inventing a fact about the library out of a request that never
     * answered — and it is the sentence that would make a reader stop looking for the hook they
     * wanted. */
    stub(() => Promise.resolve(json(500, { detail: "the database is unavailable" })));
    suggest();

    await waitFor(() => {
      expect(screen.getByText(/could not be read/i)).toBeInTheDocument();
    });
    expect(screen.getByText(/the database is unavailable/i)).toBeInTheDocument();
    expect(screen.queryByText(/records no pairing/i)).not.toBeInTheDocument();
  });

  it("says a structure records no pairing when the read succeeded and was empty", async () => {
    stub(() => Promise.resolve(json(200, [])));
    suggest();

    await waitFor(() => {
      expect(screen.getByText(/records no pairing/i)).toBeInTheDocument();
    });
    // Named as a state of the pairing, never of the library: the hooks are all still there.
    expect(screen.getByText(/all 3 approved hooks are listed/i)).toBeInTheDocument();
    expect(screen.queryByText(/could not be read/i)).not.toBeInTheDocument();
  });

  it("survives a 200 whose body is not a list, and does not read it as an empty pairing", async () => {
    /* `getJson` validates nothing, so a route that answered with an object — a proxy, a
     * mis-shaped handler — reached `hookGroups` and threw on `.map`, taking down a page that
     * may be holding an unsaved idea, three picks and a run in flight. A broken response is a
     * failed read, which is the same call `lib/api`'s `request` makes for a body it cannot
     * parse; what it is not is a structure with nothing recorded against it. */
    stub(() => Promise.resolve(json(200, { hooks: "surprise" })));
    suggest();

    await waitFor(() => {
      expect(screen.getByText(/could not be read/i)).toBeInTheDocument();
    });
    expect(screen.getByText(/something other than a list/i)).toBeInTheDocument();
    expect(screen.queryByText(/records no pairing/i)).not.toBeInTheDocument();
    // The rest of the page is still there, which is the whole point.
    expect(screen.getByRole("button", { name: /generate draft/i })).toBeInTheDocument();
  });

  it("still says the list is grouped when the pairing covers every approved hook", async () => {
    /* One group survives here, because the "every other approved hook" group is empty and is
     * filtered out — and a caption counting groups therefore read "this structure records no
     * pairing" directly above a heading saying it does. Reachable on the path the README's own
     * smoke test walks: approve one hook and one structure that records it. */
    stub(() => Promise.resolve(json(200, HOOKS)));
    suggest();

    await waitFor(() => {
      expect(screen.getByText(/grouped by what extraction recorded/i)).toBeInTheDocument();
    });
    expect(screen.queryByText(/records no pairing/i)).not.toBeInTheDocument();
  });

  it("does not claim an empty pairing while the read is still out", async () => {
    /* The state that would otherwise be indistinguishable from an empty one, which is why the
     * structure id is held beside the answer. On a slow API the caption would flash a sentence
     * that is not true and then correct itself. */
    let answer: (response: Response) => void = () => {};
    stub(() => new Promise<Response>((resolve) => (answer = resolve)));
    suggest();

    await waitFor(() => {
      expect(screen.getByText(/reading which hooks/i)).toBeInTheDocument();
    });
    expect(screen.queryByText(/records no pairing/i)).not.toBeInTheDocument();

    answer(json(200, [QUESTION]));
    await waitFor(() => {
      expect(screen.getByText(/grouped by what extraction recorded/i)).toBeInTheDocument();
    });
  });

  it("never overrides a hook that is already chosen", async () => {
    /* Narrowing suggests and never decides. The hook Suggest chose is not in the pairing here,
     * and it must still be what the next Generate sends — a picker that silently moved the
     * selection into the group it had just drawn would be the machine choosing, which is the
     * one thing this application refuses to do. */
    const fetchStub = stub(() => Promise.resolve(json(200, [QUESTION])));
    suggest();

    await waitFor(() => {
      expect(screen.getByText(/grouped by what extraction recorded/i)).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole("button", { name: /generate draft/i }));

    await waitFor(() => {
      const call = fetchStub.mock.calls.findLast(([url]) =>
        String(url).endsWith("/drafts/workflow"),
      );
      expect(call).toBeDefined();
      expect(JSON.parse(String(call![1]?.body))).toMatchObject({
        hook_id: EQUATION.id,
        structure_id: STRUCTURE.id,
      });
    });
  });
});
