import "@testing-library/jest-dom/vitest";

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Draft, Template } from "@/lib/api";

import Studio from "./Studio";

/* Where the research panel sits on the draft screen, which is two claims:
 *
 * - it is shown **before a push**, unlike the publication panel. Research is what a draft's
 *   factual claims rest on, and a reviewer who only sees it after pushing sees it after the
 *   point where it would have changed their mind;
 * - it is shown at all only when the page wired one. `research === null` is "this caller read
 *   no research", not "there is none", and a panel saying "no research is linked" on a page
 *   that never asked would be a claim about the database made by a component that made no
 *   request.
 *
 * Its own file rather than more cases in `Studio.test.tsx`: that file is 1,400 lines about the
 * generation form, and these two are about a different panel. */

vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn() } }));

afterEach(cleanup);

const TEMPLATE: Template = {
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
};

/** A draft that has never been pushed — `zernio_post_id` null, which is what hides the
 *  publication panel and must not hide this one. */
const DRAFT: Draft = {
  id: 555,
  idea: "about cat on moon hypothesis",
  mode: "directed",
  hook_text: "Hook.",
  body_text: "Body.",
  full_text: "Hook.\n\nBody.",
  visual_values: {},
  asset_values: {},
  visual_error: null,
  visual_png: null,
  has_previous_visual: false,
  zernio_post_id: null,
  revision: 1,
  pushed_revision: null,
  lineage: { hook: null, structure: null, visual: null },
  // Present on every `DraftOut`, so present on every fixture — these are required, not
  // optional: a fixture free to omit them lets a component ship a `?? "historical"`
  // fallback that no real response ever exercises.
  generation_stage: "ready",
  generation_error: null,
  gate_results: [],
  // Null, not an empty review: this fixture is a draft nobody verified.
  verification_result: null,
};

function renderStudio(research: Parameters<typeof Studio>[0]["research"]) {
  render(
    <Studio
      assets={[]}
      drafts={[]}
      initialDraft={DRAFT}
      research={research}
      templates={[TEMPLATE]}
    />,
  );
}

describe("the research panel on the draft screen", () => {
  it("is shown for a draft that has never been pushed", () => {
    renderStudio({ dossier: null, unavailable: null, jobs: [] });

    expect(screen.getByText("Sources and claims")).toBeInTheDocument();
    // And the publication panel is not, which is what makes the claim above mean something:
    // the two are gated differently and this proves it rather than assuming it.
    expect(screen.queryByText("Schedule or publish")).not.toBeInTheDocument();
  });

  it("shows the historical no-lineage state when no research was persisted", () => {
    renderStudio(null);

    expect(screen.getByText("Sources and claims")).toBeInTheDocument();
    expect(screen.getByText(/No research run is linked/)).toBeInTheDocument();
  });
});
