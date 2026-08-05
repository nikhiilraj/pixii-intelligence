import "@testing-library/jest-dom/vitest";

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import AutonomousRunPanel, { estimate, spentOnFailure } from "./AutonomousRunPanel";

/* The one operation on this screen that spends real money per press, so the things worth
 * holding are: what it says it will spend, that the server's ceiling is stated and not
 * enforced here, that the spend it reports afterwards is the observed one, and that a failed
 * run still shows what it cost — the case where the spend is the entire result.
 */

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const RESULT = {
  created: 2,
  failed: 0,
  visuals_failed: 0,
  topics: 2,
  llm_calls: 5,
  image_calls: 2,
};

function count(label: string): string {
  return screen.getByText(label).nextElementSibling?.firstElementChild?.textContent ?? "";
}

async function runIt() {
  fireEvent.click(screen.getByRole("button", { name: "Run generation…" }));
  fireEvent.click(await screen.findByRole("button", { name: "Confirm run generation" }));
}

afterEach(() => {
  vi.unstubAllGlobals();
  cleanup();
});

describe("what a run is said to cost", () => {
  it("counts one completion for the topic proposal and two per draft, and one render each", () => {
    // `run_autonomous` makes one `propose_topics` call, then `generate_draft` per topic, which
    // is a suggestion call plus a write call because an unattended run names no templates.
    expect(estimate(2)).toEqual({
      completions: "5 chat completions",
      renders: "2 image renders",
    });
    // The arithmetic the route's own docstring quotes as its reason for clamping.
    expect(estimate(500).completions).toBe("1001 chat completions");
  });

  it("says calls, never a price, and gets the singular right", () => {
    // Pure arithmetic and never displayed: `estimate(0)` is unreachable through the panel,
    // which blocks a cap below 1. It is asserted here for the singular form only —
    // `run_autonomous` returns an empty result before `propose_topics` when `cap <= 0`, so
    // "1 chat completion" is not a claim about a run of zero.
    expect(estimate(1)).toEqual({ completions: "3 chat completions", renders: "1 image render" });
    // Nothing in this application knows what a call cost, so nothing may print one.
    expect(JSON.stringify(estimate(3))).not.toMatch(/[$£€]/);
  });
});

describe("a cap below one", () => {
  it("is blocked rather than confirmed as a run that does nothing", () => {
    render(<AutonomousRunPanel credentials={{ azure_chat: true, cloudflare_rendering: true }} ceiling={2} />);

    // `min={1}` on a number input is a hint: clearing the field yields `""`, and `Number("")`
    // is 0. Without the block the confirmation would promise "at most 0 drafts" beside "at
    // most 1 chat completion" and post `?cap=0`, which the backend answers with an empty
    // result before making any call at all.
    fireEvent.change(screen.getByLabelText("draft cap"), { target: { value: "" } });

    expect(screen.getByRole("button", { name: "Run generation…" })).toBeDisabled();
    expect(screen.getByText(/Set a cap of at least 1/)).toBeInTheDocument();
  });
});

describe("the ceiling is stated, never applied", () => {
  it("posts the clamped number and says the clamp is the server's", async () => {
    const fetchSpy = vi.fn(() => Promise.resolve(jsonResponse(200, RESULT)));
    vi.stubGlobal("fetch", fetchSpy);
    render(<AutonomousRunPanel credentials={{ azure_chat: true, cloudflare_rendering: true }} ceiling={2} />);

    fireEvent.change(screen.getByLabelText("draft cap"), { target: { value: "50" } });

    expect(screen.getByText(/The configured ceiling is 2, so this run will produce at most 2/)).toBeInTheDocument();
    await runIt();

    await waitFor(() => expect(fetchSpy).toHaveBeenCalledTimes(1));
    // The client says 2 because the server would do 2. It is not what makes it 2 — a client
    // sending 50 would get 2 back anyway, which is why this is a statement and not a guard.
    const [url] = fetchSpy.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toContain("/drafts/autonomous-run?cap=2");
  });

  it("shows the number as typed when the ceiling could not be read", () => {
    render(<AutonomousRunPanel credentials={null} ceiling={null} />);

    fireEvent.change(screen.getByLabelText("draft cap"), { target: { value: "9" } });

    expect(screen.getByText(/configured ceiling could not be read/)).toBeInTheDocument();
    // Seeded at 1, not at a guess: guessing high on a paid operation is the wrong way to be
    // wrong.
    expect(screen.getByText(/enforced by the route, never here/)).toBeInTheDocument();
  });

  it("freezes the cap when the confirmation opens, so the dialog describes the run that goes out", async () => {
    const fetchSpy = vi.fn(() => Promise.resolve(jsonResponse(200, RESULT)));
    vi.stubGlobal("fetch", fetchSpy);
    render(<AutonomousRunPanel credentials={{ azure_chat: true, cloudflare_rendering: true }} ceiling={5} />);

    fireEvent.change(screen.getByLabelText("draft cap"), { target: { value: "2" } });
    fireEvent.click(screen.getByRole("button", { name: "Run generation…" }));

    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent("at most 2");
    expect(dialog).toHaveTextContent("at most 5 chat completions and 2 image renders");
    expect(dialog).toHaveTextContent("an unattended run cannot reach Zernio");
  });
});

describe("prerequisites", () => {
  it("separates the credential that stops the run from the one that costs it its pictures", () => {
    render(<AutonomousRunPanel credentials={{ azure_chat: false, cloudflare_rendering: false }} ceiling={2} />);

    expect(screen.getByText(/Nothing can be written without it/)).toBeInTheDocument();
    expect(screen.getByText(/arrive with no visual and will need redrawing/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run generation…" })).toBeDisabled();
  });
});

describe("what the run reports afterwards", () => {
  it("reports every field, including the drafts that arrived with no picture", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve(
          jsonResponse(200, { ...RESULT, created: 3, visuals_failed: 3, topics: 3, llm_calls: 7, image_calls: 3 }),
        ),
      ),
    );
    render(<AutonomousRunPanel credentials={{ azure_chat: true, cloudflare_rendering: true }} ceiling={3} />);

    await runIt();
    await screen.findByRole("status");

    // "3 created" with every image missing is the failure the backend reports this beside
    // `created` to prevent, so the screen has to carry it too.
    expect(count("Created")).toBe("3");
    expect(count("Without a visual")).toBe("3");
    expect(count("Failed")).toBe("0");
    expect(count("Chat completions")).toBe("7");
    expect(count("Image renders")).toBe("3");
  });

  it("prints a run that produced nothing as zeros, not as an absence", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve(
          jsonResponse(200, { created: 0, failed: 0, visuals_failed: 0, topics: 0, llm_calls: 1, image_calls: 0 }),
        ),
      ),
    );
    render(<AutonomousRunPanel credentials={{ azure_chat: true, cloudflare_rendering: true }} ceiling={2} />);

    await runIt();
    const readout = await screen.findByRole("status");

    // A run that proposed no topics still bought its proposal call. Every one of these is a
    // measurement.
    expect(count("Topics")).toBe("0");
    expect(count("Created")).toBe("0");
    expect(count("Chat completions")).toBe("1");
    // No em dash in any *value*. Scoped to the `dd`s rather than the card, because the note
    // underneath is prose and an em dash in a sentence is punctuation — it is a value printed
    // as `—` that would be the lie.
    for (const value of readout.querySelectorAll("dd")) {
      expect(value.textContent).not.toContain("—");
    }
  });
});

describe("the failure path, where the spend is the whole result", () => {
  it("pulls llm_calls and image_calls out of a 502 detail", () => {
    expect(
      spentOnFailure({
        ok: false,
        kind: "http",
        status: 502,
        message: "no approved templates, llm_calls: 1, image_calls: 0",
        detail: { error: "no approved templates", llm_calls: 1, image_calls: 0 },
      }),
    ).toEqual({ llm: 1, image: 0 });
  });

  it("reports no spend for a network failure, which spent nothing", () => {
    expect(spentOnFailure({ ok: false, kind: "network", message: "fetch failed" })).toBeNull();
    // And for a proxy's HTML 502, which carries no JSON detail at all.
    expect(
      spentOnFailure({ ok: false, kind: "http", status: 502, message: "request failed (502)", detail: null }),
    ).toBeNull();
  });

  it("shows the reason and the money on screen, and no result table", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve(
          jsonResponse(502, {
            detail: { error: "no approved templates of every kind", llm_calls: 1, image_calls: 0 },
          }),
        ),
      ),
    );
    render(<AutonomousRunPanel credentials={{ azure_chat: true, cloudflare_rendering: true }} ceiling={2} />);

    await runIt();

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("no approved templates of every kind");
    expect(alert).toHaveTextContent("Chat completions spent");
    expect(alert).toHaveTextContent("rolled back with the request");
    // Both directions: a panel that always drew the counts table would pass every line above.
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });
});
