import "@testing-library/jest-dom/vitest";

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ApiResult } from "@/lib/api";

import ConfirmedAction, { type Prerequisite } from "./ConfirmedAction";

/* The confirmation guard, which is the whole reason this component exists rather than four
 * buttons calling `postJson`.
 *
 * The assertion that matters is negative and is easy to write in a way that cannot fail:
 * "pressing the named button sends nothing" is only a real test if the run function would
 * otherwise have been called. So every test here counts calls on a spy that is wired all the
 * way through `compose`, and the guard test asserts zero *and then* asserts one after Confirm —
 * a component that never ran anything at all would pass the first half and fail the second.
 */

function met(credential: string): Prerequisite {
  return { credential, label: credential, met: true, consequence: `${credential} is missing` };
}

function missing(credential: string): Prerequisite {
  return { credential, label: credential, met: false, consequence: `${credential} is missing` };
}

/** A `ConfirmedAction` wired to a spy, with everything else at its least interesting. */
function harness(
  run: () => Promise<ApiResult<{ created: number }>>,
  overrides: {
    prerequisites?: Prerequisite[] | null;
    blockedReason?: string | null;
  } = {},
) {
  return (
    <ConfirmedAction<{ created: number }>
      title="Pull the corpus"
      lede="Reads the account."
      actionLabel="Pull from Zernio"
      consequence="This reaches Zernio."
      rerun="Safe to run again."
      prerequisites={overrides.prerequisites === undefined ? [met("zernio")] : overrides.prerequisites}
      blockedReason={overrides.blockedReason ?? null}
      compose={() => ({ facts: [{ label: "Reads", value: "the account" }], run })}
      renderResult={(data) => <p>created {data.created}</p>}
    />
  );
}

const ok: ApiResult<{ created: number }> = { ok: true, data: { created: 0 } };

afterEach(cleanup);

describe("the button that names the action does not fire it", () => {
  it("sends nothing until the confirmation is confirmed", async () => {
    const run = vi.fn(() => Promise.resolve(ok));
    render(harness(run));

    fireEvent.click(screen.getByRole("button", { name: "Pull from Zernio…" }));

    // The dialog is open and the request has not been made. Both halves are asserted: the
    // first alone passes for a component that does nothing at all.
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
    expect(run).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Confirm pull from zernio" }));
    await waitFor(() => expect(run).toHaveBeenCalledTimes(1));
  });

  it("sends nothing when the confirmation is cancelled", async () => {
    const run = vi.fn(() => Promise.resolve(ok));
    render(harness(run));

    fireEvent.click(screen.getByRole("button", { name: "Pull from Zernio…" }));
    fireEvent.click(await screen.findByRole("button", { name: "Cancel" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(run).not.toHaveBeenCalled();
  });

  it("shows the action, what it reaches and what a second run does before firing", async () => {
    render(harness(() => Promise.resolve(ok)));

    fireEvent.click(screen.getByRole("button", { name: "Pull from Zernio…" }));
    const dialog = await screen.findByRole("dialog");

    expect(dialog).toHaveTextContent("This reaches Zernio.");
    expect(dialog).toHaveTextContent("Safe to run again.");
    expect(dialog).toHaveTextContent("the account");
  });
});

describe("a request in flight", () => {
  it("cannot be submitted twice", async () => {
    // Never resolves, so the component stays busy for the whole test — which is exactly the
    // window a double press lands in.
    const run = vi.fn(() => new Promise<ApiResult<{ created: number }>>(() => {}));
    render(harness(run));

    fireEvent.click(screen.getByRole("button", { name: "Pull from Zernio…" }));
    const confirm = await screen.findByRole("button", { name: "Confirm pull from zernio" });
    fireEvent.click(confirm);

    await waitFor(() => expect(screen.getByRole("button", { name: "Running…" })).toBeDisabled());
    fireEvent.click(screen.getByRole("button", { name: "Running…" }));

    expect(run).toHaveBeenCalledTimes(1);
  });
});

describe("configuration prerequisites", () => {
  it("says which credential is missing, and disables the control rather than failing on click", () => {
    render(harness(() => Promise.resolve(ok), { prerequisites: [missing("zernio")] }));

    expect(screen.getByText("zernio not configured")).toBeInTheDocument();
    expect(screen.getByText("zernio is missing")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Pull from Zernio…" })).toBeDisabled();
  });

  it("leaves the control enabled when the configuration could not be read", () => {
    // `null` is a failed `/health`, which is not `false`. Disabling here would hide a
    // capability that is working because one unrelated request did not arrive — the
    // `publishing === null` rule from PublishPanel.
    render(harness(() => Promise.resolve(ok), { prerequisites: null }));

    expect(screen.getByText("Configuration could not be read.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Pull from Zernio…" })).toBeEnabled();
    // And it must not claim anything is missing.
    expect(screen.queryByText("zernio not configured")).not.toBeInTheDocument();
  });

  it("distinguishes a missing credential from an unreadable one in words", () => {
    const { unmount } = render(
      harness(() => Promise.resolve(ok), { prerequisites: [missing("zernio")] }),
    );
    expect(screen.queryByText("Configuration could not be read.")).not.toBeInTheDocument();
    unmount();

    render(harness(() => Promise.resolve(ok), { prerequisites: null }));
    expect(screen.getByText("Configuration could not be read.")).toBeInTheDocument();
  });
});

describe("an input that is not ready", () => {
  it("blocks the action and says why, without pretending a credential is missing", () => {
    render(
      harness(() => Promise.resolve(ok), { blockedReason: "Paste a scrape above to enable this." }),
    );

    expect(screen.getByRole("button", { name: "Pull from Zernio…" })).toBeDisabled();
    expect(screen.getByText("Paste a scrape above to enable this.")).toBeInTheDocument();
    expect(screen.getByText("zernio configured")).toBeInTheDocument();
  });
});

describe("what a result and a failure look like", () => {
  it("renders the result, including a measured zero", async () => {
    render(harness(() => Promise.resolve(ok)));

    fireEvent.click(screen.getByRole("button", { name: "Pull from Zernio…" }));
    fireEvent.click(await screen.findByRole("button", { name: "Confirm pull from zernio" }));

    // `0` is the run having happened and created nothing. An em dash here would claim nobody
    // looked.
    expect(await screen.findByText("created 0")).toBeInTheDocument();
  });

  it("renders an HTTP refusal as a refusal, never as a result", async () => {
    render(
      harness(() =>
        Promise.resolve({
          ok: false,
          kind: "http",
          status: 502,
          message: "Zernio returned 500",
        } as ApiResult<{ created: number }>),
      ),
    );

    fireEvent.click(screen.getByRole("button", { name: "Pull from Zernio…" }));
    fireEvent.click(await screen.findByRole("button", { name: "Confirm pull from zernio" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("The request was refused — HTTP 502");
    expect(alert).toHaveTextContent("Zernio returned 500");
    expect(screen.queryByText(/^created/)).not.toBeInTheDocument();
  });

  it("says a network failure sent nothing, which an HTTP failure cannot say", async () => {
    render(
      harness(() =>
        Promise.resolve({
          ok: false,
          kind: "network",
          message: "fetch failed",
        } as ApiResult<{ created: number }>),
      ),
    );

    fireEvent.click(screen.getByRole("button", { name: "Pull from Zernio…" }));
    fireEvent.click(await screen.findByRole("button", { name: "Confirm pull from zernio" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("The API could not be reached.");
    expect(alert).toHaveTextContent("nothing outside this machine was touched");
  });
});
