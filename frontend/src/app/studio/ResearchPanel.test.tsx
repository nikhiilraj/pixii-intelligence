import "@testing-library/jest-dom/vitest";

import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { Citation, Claim, Dossier, ResearchSource } from "@/lib/api";

import ResearchPanel, { coverage, measured, type ResearchView } from "./ResearchPanel";

/* What this file is defending, in one sentence: **a claim with nothing behind it, rendered as
 * though something were.**
 *
 * That is not the same target as "the panel renders". `research.py` restructured a table so
 * that an uncited claim is a state you can query rather than a silence a reader has to notice,
 * and every way this component can undo that is silent on screen: a badge that says nothing,
 * an empty citation list that reads as "still loading", a `0` where nothing was measured, a
 * link to the URL that was asked for rather than the one that answered.
 *
 * The assertions are scoped with `within()` for the reason PublishPanel's are: this panel puts
 * a dozen numbers on screen, and "`0` appears nowhere" would pass or fail for reasons that have
 * nothing to do with the field under test. */

afterEach(cleanup);

function source(overrides: Partial<ResearchSource> = {}): ResearchSource {
  return {
    id: 1,
    url: "https://example.com/final-article",
    requested_url: "https://example.com/final-article",
    title: "What the Act changed",
    publisher: "example.com",
    published_at: null,
    fetched_at: "2026-08-05T09:31:00",
    trust_tier: null,
    content_hash: "a".repeat(64),
    ...overrides,
  };
}

function claim(overrides: Partial<Claim> = {}): Claim {
  return {
    id: 10,
    text: "Providers must publish training summaries.",
    status: "supported",
    supporting_citation_ids: [100],
    contradicting_citation_ids: [],
    ...overrides,
  };
}

function citation(overrides: Partial<Citation> = {}): Citation {
  return {
    id: 100,
    claim_id: 10,
    source_id: 1,
    stance: "supports",
    span: "providers of general purpose models must publish training summaries",
    source_content_hash: "a".repeat(64),
    ...overrides,
  };
}

function dossier(overrides: Partial<Dossier> = {}): Dossier {
  return {
    job_id: 4,
    question: "What did the EU AI Act change for model providers in 2026?",
    mode: "light",
    recommended_mode: "light",
    mode_signals: ["number", "law"],
    state: "completed",
    researched_at: "2026-08-05T09:30:00",
    freshness_days: null,
    sources: [source()],
    claims: [claim()],
    citations: [citation()],
    unknowns: [],
    contradictions: [],
    spend: {
      queries: 2,
      sources_found: 3,
      sources_fetched: 1,
      llm_calls: 2,
      budget_exhausted: null,
    },
    ...overrides,
  };
}

function view(overrides: Partial<ResearchView> = {}): ResearchView {
  return { dossier: dossier(), unavailable: null, ...overrides };
}

/** The `<dd>` beside a `<dt>` with this text — the only way to assert on one number out of the
 *  dozen this panel prints. `nextElementSibling` because the grid is a real `<dl>`. */
function valueFor(label: string): HTMLElement {
  const term = screen.getByText(label, { selector: "dt" });
  const value = term.nextElementSibling;
  if (!(value instanceof HTMLElement)) throw new Error(`no <dd> after <dt>${label}`);
  return value;
}

// --- an uncited claim is visibly uncited --------------------------------------------------------

describe("an uncited claim", () => {
  it("is badged with a word that says so", () => {
    render(
      <ResearchPanel
        draftId={7}
        research={view({
          dossier: dossier({
            claims: [claim({ status: "unsupported", supporting_citation_ids: [] })],
            citations: [],
            unknowns: [{ text: "Providers must publish training summaries.", claim_id: 10 }],
          }),
        })}
      />,
    );

    const claims = screen.getByText("Claims", { selector: "p" }).parentElement;
    expect(within(claims as HTMLElement).getByText("Uncited")).toBeInTheDocument();
  });

  it("is not badged the same as a claim that is supported", () => {
    /* The assertion that makes the one above mean something. "Badge every claim Uncited" passes
     * the first test and is the worst possible version of this panel. */
    render(
      <ResearchPanel
        draftId={7}
        research={view({
          dossier: dossier({
            claims: [
              claim({ id: 10, text: "Cited thing.", status: "supported" }),
              claim({
                id: 11,
                text: "Uncited thing.",
                status: "unsupported",
                supporting_citation_ids: [],
              }),
            ],
          }),
        })}
      />,
    );

    const cited = screen.getByText("Cited thing.").closest("li") as HTMLElement;
    const uncited = screen.getByText("Uncited thing.").closest("li") as HTMLElement;

    expect(within(cited).queryByText("Uncited")).not.toBeInTheDocument();
    expect(within(cited).getByText("Supported")).toBeInTheDocument();
    expect(within(uncited).getByText("Uncited")).toBeInTheDocument();
  });

  it("says in words that nothing supports it, rather than showing an empty list", () => {
    /* An empty list under a claim reads as "citations have not loaded". The sentence is what
     * makes the absence a finding rather than a gap in the rendering. */
    render(
      <ResearchPanel
        draftId={7}
        research={view({
          dossier: dossier({
            claims: [claim({ status: "unsupported", supporting_citation_ids: [] })],
            citations: [],
          }),
        })}
      />,
    );

    expect(screen.getByText(/Nothing in this run supports this/)).toBeInTheDocument();
  });

  it("is counted in the coverage summary", () => {
    render(
      <ResearchPanel
        draftId={7}
        research={view({
          dossier: dossier({
            claims: [
              claim({ id: 10, status: "supported" }),
              claim({ id: 11, text: "b", status: "unsupported", supporting_citation_ids: [] }),
              claim({ id: 12, text: "c", status: "unsupported", supporting_citation_ids: [] }),
            ],
          }),
        })}
      />,
    );

    expect(valueFor("Uncited")).toHaveTextContent("2");
    expect(valueFor("Claims")).toHaveTextContent("3");
  });
});

describe("a refuted claim", () => {
  it("is not rendered as an uncited one", () => {
    /* Both have an empty `supporting_citation_ids`, which is why the panel reads `status` and
     * not the list. Calling a claim two sources contradict "uncited" says the opposite of what
     * happened to it. */
    render(
      <ResearchPanel
        draftId={7}
        research={view({
          dossier: dossier({
            claims: [
              claim({
                status: "refuted",
                supporting_citation_ids: [],
                contradicting_citation_ids: [100],
              }),
            ],
            citations: [citation({ stance: "contradicts" })],
          }),
        })}
      />,
    );

    const row = screen.getByText("Providers must publish training summaries.").closest("li");
    expect(within(row as HTMLElement).getByText("Contradicted")).toBeInTheDocument();
    expect(within(row as HTMLElement).queryByText("Uncited")).not.toBeInTheDocument();
  });
});

// --- `—` where nothing was measured, and `0` where something was --------------------------------

describe("spend", () => {
  it("prints an em dash where nothing was ever measured", () => {
    render(
      <ResearchPanel
        draftId={7}
        research={view({
          dossier: dossier({
            mode: "none",
            recommended_mode: "none",
            mode_signals: [],
            claims: [],
            citations: [],
            sources: [],
            spend: {
              queries: null,
              sources_found: null,
              sources_fetched: null,
              llm_calls: null,
              budget_exhausted: null,
            },
          }),
        })}
      />,
    );

    expect(valueFor("Queries issued")).toHaveTextContent("—");
    expect(valueFor("Sources found")).toHaveTextContent("—");
    expect(valueFor("Sources fetched")).toHaveTextContent("—");
    expect(valueFor("Model calls")).toHaveTextContent("—");
    // Asserted per field rather than as "no 0 anywhere": every one of them is a separate way to
    // present an absence as a measurement, and a panel that gets three right is still wrong.
    expect(valueFor("Queries issued")).not.toHaveTextContent("0");
    expect(valueFor("Sources found")).not.toHaveTextContent("0");
  });

  it("prints a measured zero as zero", () => {
    /* The half that `value || "—"` fails. A search loop that ran and found nothing is a real
     * finding, and an em dash here would erase it — the same rule read backwards. */
    render(
      <ResearchPanel
        draftId={7}
        research={view({
          dossier: dossier({
            spend: {
              queries: 2,
              sources_found: 0,
              sources_fetched: 0,
              llm_calls: 1,
              budget_exhausted: null,
            },
          }),
        })}
      />,
    );

    expect(valueFor("Sources found")).toHaveTextContent("0");
    expect(valueFor("Sources found")).not.toHaveTextContent("—");
    expect(valueFor("Sources fetched")).toHaveTextContent("0");
  });

  it("names the ceiling that stopped the run early", () => {
    render(
      <ResearchPanel
        draftId={7}
        research={view({
          dossier: dossier({
            spend: {
              queries: 2,
              sources_found: 9,
              sources_fetched: 4,
              llm_calls: 2,
              budget_exhausted: "fetches",
            },
          }),
        })}
      />,
    );

    expect(valueFor("Stopped early by")).toHaveTextContent("fetches");
    expect(screen.getByText(/hit its fetches ceiling/)).toBeInTheDocument();
  });

  it("counts nothing at all where there are no claims", () => {
    /* `—`, not four zeros. This response cannot tell a claim pass that proposed nothing from
     * one that never ran, so a count of `0 supported` would be a measurement nobody took. */
    render(
      <ResearchPanel
        draftId={7}
        research={view({ dossier: dossier({ claims: [], citations: [] }) })}
      />,
    );

    expect(screen.getByText(/No claims, so every count here is/)).toBeInTheDocument();
    expect(screen.queryByText("Supported", { selector: "dt" })).not.toBeInTheDocument();
  });
});

describe("measured", () => {
  it("distinguishes a null from a zero", () => {
    expect(measured(null)).toBe("—");
    expect(measured(0)).toBe("0");
    expect(measured(4)).toBe("4");
  });
});

describe("coverage", () => {
  it("is null for an empty claim list rather than four zeros", () => {
    expect(coverage([])).toBeNull();
  });

  it("counts each status, including the zeros that were measured", () => {
    expect(coverage([claim({ status: "supported" }), claim({ status: "unsupported" })])).toEqual({
      supported: 1,
      disputed: 0,
      refuted: 0,
      unsupported: 1,
    });
  });
});

// --- a source is what was read, not what was asked for -----------------------------------------

describe("a source", () => {
  it("links the URL that served the bytes, not the one that was requested", () => {
    /* The fixture redirects, or this test cannot fail: with the two URLs equal, a component
     * linking the requested one passes. Asserted on `href`, not on the link's text. */
    render(
      <ResearchPanel
        draftId={7}
        research={view({
          dossier: dossier({
            sources: [
              source({
                url: "https://example.com/final-article",
                requested_url: "https://short.link/xyz",
              }),
            ],
          }),
        })}
      />,
    );

    for (const link of screen.getAllByRole("link", { name: /What the Act changed/ })) {
      expect(link).toHaveAttribute("href", "https://example.com/final-article");
    }
    // The requested address is still on screen — the hop is provenance — as text.
    expect(screen.getByText("https://short.link/xyz")).toBeInTheDocument();
    expect(screen.getByText("https://short.link/xyz").closest("a")).toBeNull();
  });

  it("is never presented as verified because it was fetched", () => {
    render(<ResearchPanel draftId={7} research={view()} />);

    expect(valueFor("Trust tier")).toHaveTextContent("—");
    expect(valueFor("Published")).toHaveTextContent("—");
    // What is actually known, and what makes the fetch checkable at all.
    expect(valueFor("Content hash")).toHaveTextContent("a".repeat(64));
    expect(valueFor("Fetched")).toHaveTextContent("2026-08-05 09:31 UTC");
  });

  it("shows the fetched span as text and not as markup", () => {
    /* `fetching.py` stripped active content out of these pages; rendering the quotation as HTML
     * here would put it back. The span is someone else's document, quoted. */
    render(
      <ResearchPanel
        draftId={7}
        research={view({
          dossier: dossier({
            citations: [citation({ span: "<img src=x onerror=alert(1)> the rule applies" })],
          }),
        })}
      />,
    );

    expect(screen.getByText(/<img src=x onerror=alert\(1\)> the rule applies/)).toBeInTheDocument();
    expect(document.querySelector("img")).toBeNull();
  });
});

// --- findings, not failures ----------------------------------------------------------------------

describe("contradictions and unknowns", () => {
  it("shows a contradiction as a finding rather than an error", () => {
    const disputed = claim({
      id: 12,
      text: "Enforcement begins in August.",
      status: "disputed",
      supporting_citation_ids: [100],
      contradicting_citation_ids: [101],
    });
    render(
      <ResearchPanel
        draftId={7}
        research={view({
          dossier: dossier({
            claims: [disputed],
            contradictions: [disputed],
            citations: [citation(), citation({ id: 101, claim_id: 12, stance: "contradicts" })],
          }),
        })}
      />,
    );

    const section = screen.getByText("Contradictions", { selector: "p" }).parentElement;
    expect(within(section as HTMLElement).getByText("Enforcement begins in August.")).toBeInTheDocument();
    // Not in an alert. A run whose sources disagreed did its job; the only `role="alert"` this
    // panel ever draws is for a run that died or a read that failed.
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("keeps a contradiction in the claims list as well", () => {
    /* The two lists overlap on purpose. Moving a disputed claim out of `claims` would let a
     * reader of that list miss the most interesting row in the dossier. */
    const disputed = claim({ id: 12, text: "Enforcement begins in August.", status: "disputed" });
    render(
      <ResearchPanel
        draftId={7}
        research={view({ dossier: dossier({ claims: [disputed], contradictions: [disputed] }) })}
      />,
    );

    expect(screen.getAllByText("Enforcement begins in August.")).toHaveLength(2);
  });

  it("distinguishes an open question from a claim nothing supported", () => {
    render(
      <ResearchPanel
        draftId={7}
        research={view({
          dossier: dossier({
            unknowns: [
              { text: "Whether fine-tunes count.", claim_id: null },
              { text: "Providers must publish training summaries.", claim_id: 10 },
            ],
          }),
        })}
      />,
    );

    expect(screen.getByText(/an open question; no claim was made about it/)).toBeInTheDocument();
    expect(screen.getByText(/claim 10, which nothing supports/)).toBeInTheDocument();
  });

  it("shows a failed run as an error, which contradictions and unknowns are not", () => {
    render(
      <ResearchPanel draftId={7} research={view({ dossier: dossier({ state: "failed" }) })} />,
    );

    expect(screen.getByRole("alert")).toHaveTextContent("This research run failed.");
  });
});

// --- order is chronology, never ranking ----------------------------------------------------------

describe("order", () => {
  it("renders claims in the dossier's own order, with the uncited one where it was written", () => {
    /* Sorting the uncited claims to the top is one line and is the tempting move. `dossier()`'s
     * docstring refuses it — the project's never-rank rule applied to evidence — and this is
     * the assertion that fails if someone adds it. */
    render(
      <ResearchPanel
        draftId={7}
        research={view({
          dossier: dossier({
            claims: [
              claim({ id: 10, text: "One." }),
              claim({ id: 11, text: "Two.", status: "unsupported", supporting_citation_ids: [] }),
              claim({ id: 12, text: "Three." }),
            ],
          }),
        })}
      />,
    );

    const claims = screen.getByText("Claims", { selector: "p" }).parentElement as HTMLElement;
    // The claim rows only. `getAllByRole("listitem")` also returns each claim's citations, which
    // are nested `<li>`s, and their order is not what this asserts.
    const rows = [...claims.querySelectorAll(":scope > ul > li")];
    expect(rows.map((row) => row.textContent?.slice(0, 40))).toEqual([
      expect.stringContaining("One."),
      expect.stringContaining("Two."),
      expect.stringContaining("Three."),
    ]);
  });
});

// --- persisted states with no dossier ------------------------------------------------------------

describe("with no dossier", () => {
  it("says no run is linked rather than that none exists", () => {
    render(<ResearchPanel draftId={7} research={{ dossier: null, unavailable: null }} />);

    expect(screen.getByText(/No research run is linked to this draft/)).toBeInTheDocument();
    expect(screen.getByText(/expected when the brief selected/)).toBeInTheDocument();
  });

  it("reports a failed read as a failed read, not as an absence of research", () => {
    render(
      <ResearchPanel
        draftId={7}
        research={{ dossier: null, unavailable: "HTTP 404: no research job 9" }}
      />,
    );

    expect(screen.getByRole("alert")).toHaveTextContent("no research job 9");
    expect(screen.getByText(/could not be opened/)).toBeInTheDocument();
    expect(screen.queryByText(/expected when the brief selected/)).not.toBeInTheDocument();
  });

  it("does not offer unrelated runs as draft lineage", () => {
    /* This used to hand the panel a `jobs` list and assert no link to it rendered. `ResearchView`
     * no longer has that field — nothing read it and every caller passed `[]` — so the guarantee
     * moved from an assertion to the type: there is nowhere to put an unrelated run. What is
     * still worth asserting is the sentence that tells a reviewer the run on screen is the
     * draft's own, which is the part a redesign could quietly drop. */
    render(<ResearchPanel draftId={7} research={{ dossier: null, unavailable: null }} />);

    expect(screen.getByText(/never selected from the URL/)).toBeInTheDocument();
  });
});

// --- what the mode says ---------------------------------------------------------------------------

describe("the mode", () => {
  it("shows what ran beside what the floor asked for", () => {
    /* One field would make a silent downgrade unanswerable afterwards, which is why the job row
     * stores two — and why they are shown together even when they agree. */
    render(
      <ResearchPanel
        draftId={7}
        research={view({ dossier: dossier({ mode: "light", recommended_mode: "light" }) })}
      />,
    );

    expect(valueFor("Mode")).toHaveTextContent("light · floor asked for light (number, law)");
  });

  it("says which run is on screen", () => {
    // Otherwise the only answer is the address bar, which does not travel with a screenshot.
    render(<ResearchPanel draftId={7} research={view()} />);

    expect(valueFor("Run")).toHaveTextContent("4");
  });

  it("prints an em dash for a freshness policy nobody stated", () => {
    render(<ResearchPanel draftId={7} research={view()} />);

    expect(valueFor("Freshness policy")).toHaveTextContent("—");
  });
});
