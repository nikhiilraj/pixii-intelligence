import "@testing-library/jest-dom/vitest";

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CorpusIngestPanel, MetricsSyncPanel } from "./ZernioPanels";

/* The two Zernio pulls. What is worth holding here is not the wiring — `ConfirmedAction` owns
 * the confirmation and its own test — but the three things these panels decide:
 *
 *   1. the request they actually compose, including the flag the checkbox sets;
 *   2. that every count they report is printed as a number, `0` included, because a run that
 *      created nothing is a measurement and not an absence;
 *   3. that a refused request never draws a result table full of zeros, which is the same
 *      error wearing a success.
 */

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const INGEST = {
  fetched: 50,
  created: 0,
  updated: 50,
  history_fetched: 107,
  history_recovered: 0,
};

const SYNC = { fetched: 50, snapshots: 50, went_live: 0, created: 0, updated: 50 };

/** The number printed against one label in a `Counts` readout.
 *
 *  Read out of the `dd` rather than off the card's text, because a `dt`/`dd` pair has no
 *  whitespace between it in the DOM — `toHaveTextContent("Created 0")` matches "Created0" only
 *  by accident of normalisation and does not — and because the value and its hint are separate
 *  elements. This asks the question the test means: what number is against this label. */
function count(label: string): string {
  return screen.getByText(label).nextElementSibling?.firstElementChild?.textContent ?? "";
}

/** Press the named button, then Confirm. Returns the fetch spy's calls. */
async function runAction(name: string) {
  fireEvent.click(screen.getByRole("button", { name: `${name}…` }));
  fireEvent.click(await screen.findByRole("button", { name: `Confirm ${name.toLowerCase()}` }));
}

afterEach(() => {
  vi.unstubAllGlobals();
  cleanup();
});

describe("the corpus ingest", () => {
  it("posts with_media as the checkbox has it, not as the route defaults it", async () => {
    const fetchSpy = vi.fn(() => Promise.resolve(jsonResponse(200, INGEST)));
    vi.stubGlobal("fetch", fetchSpy);
    render(<CorpusIngestPanel credentials={{ zernio: true }} />);

    // Ticked on arrival, because the route's own default is true and the media is what visual
    // extraction reads. Untick it and the request has to follow.
    fireEvent.click(screen.getByRole("checkbox"));
    await runAction("Pull from Zernio");

    await waitFor(() => expect(fetchSpy).toHaveBeenCalledTimes(1));
    const [url] = fetchSpy.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toContain("/corpus/ingest?with_media=false");
  });

  it("names the media decision on the confirmation, before it is made", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(jsonResponse(200, INGEST))));
    render(<CorpusIngestPanel credentials={{ zernio: true }} />);

    fireEvent.click(screen.getByRole("button", { name: "Pull from Zernio…" }));

    expect(await screen.findByRole("dialog")).toHaveTextContent("downloaded with each post");
  });

  it("prints a created count of zero as 0, never as an em dash", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(jsonResponse(200, INGEST))));
    render(<CorpusIngestPanel credentials={{ zernio: true }} />);

    await runAction("Pull from Zernio");

    // `created: 0` beside `fetched: 50` is the normal result of a repeat run — 50 posts read,
    // all of them already here. An em dash would say the ingest never looked.
    const readout = await screen.findByRole("status");
    expect(count("Created")).toBe("0");
    expect(count("Fetched")).toBe("50");
    expect(count("Recovered")).toBe("0");
    expect(readout).not.toHaveTextContent("—");
  });

  it("says Zernio is unconfigured rather than letting the button fail on click", () => {
    render(<CorpusIngestPanel credentials={{ zernio: false }} />);

    expect(screen.getByText("Zernio not configured")).toBeInTheDocument();
    expect(screen.getByText(/ZERNIO_API_KEY` is not set/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Pull from Zernio…" })).toBeDisabled();
  });

  it("leaves the control enabled when /health itself could not be read", () => {
    render(<CorpusIngestPanel credentials={null} />);

    expect(screen.getByRole("button", { name: "Pull from Zernio…" })).toBeEnabled();
    expect(screen.queryByText("Zernio not configured")).not.toBeInTheDocument();
  });
});

describe("the metrics sync", () => {
  it("reports what a refusal was, and no counts at all", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.resolve(jsonResponse(502, { detail: "getlate.dev returned 500" }))),
    );
    render(<MetricsSyncPanel credentials={{ zernio: true }} />);

    await runAction("Sync metrics");

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("getlate.dev returned 500");
    // Both directions. A component that always renders the counts table would pass the line
    // above and fail this one — which is the failure-rendered-as-empty-dataset bug.
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    expect(screen.queryByText("Snapshots")).not.toBeInTheDocument();
  });

  it("prints a newly-live count of zero as 0", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(jsonResponse(200, SYNC))));
    render(<MetricsSyncPanel credentials={{ zernio: true }} />);

    await runAction("Sync metrics");

    // Zero here means no pushed draft has been published since the last sync — the honest
    // answer for a system whose circuit has never closed, and a measurement either way.
    const readout = await screen.findByRole("status");
    expect(readout).toBeInTheDocument();
    expect(count("Newly live")).toBe("0");
    expect(count("Snapshots")).toBe("50");
  });

  it("says on the confirmation that readings accumulate rather than overwrite", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(jsonResponse(200, SYNC))));
    render(<MetricsSyncPanel credentials={{ zernio: true }} />);

    fireEvent.click(screen.getByRole("button", { name: "Sync metrics…" }));

    expect(await screen.findByRole("dialog")).toHaveTextContent(
      "readings accumulate rather than overwrite",
    );
  });
});
