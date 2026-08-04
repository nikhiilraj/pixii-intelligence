import "@testing-library/jest-dom/vitest";

import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ApiFailure, Draft, Publication, PublishingTarget } from "@/lib/api";

import PublishPanel, { classify, resolveUtc, stamp, utcLabel } from "./PublishPanel";

/* What this file is defending, in one sentence: **a command that goes out saying something
 * other than what the screen said.**
 *
 * That is not the same target as "the panel renders". Three of the assertions here read the
 * *request body* rather than the DOM, because every failure this component can have is silent
 * on screen — a missing `revision` publishes words nobody confirmed and renders identically, a
 * command fired without its confirmation looks like a command fired with one, and a 409 shown
 * as a 502 is a sentence, not a broken page.
 *
 * What is NOT tested here, deliberately: the Dialog's focus trap, Escape, scroll lock and focus
 * return, and the `<datalist>` dropdown. Those belong to Radix and to the platform, jsdom only
 * approximates focus, and the native datetime picker has no DOM to assert on at all. They were
 * exercised by hand in Chrome; the slice report says what was seen. */

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const DRAFT: Draft = {
  id: 555,
  idea: "Amazon bundles might be the anti-coupon strategy",
  mode: "directed",
  hook_text: "Bundles are the anti-coupon.",
  body_text: "Same margin, no discount habit.",
  full_text: "Bundles are the anti-coupon.\n\nSame margin, no discount habit.",
  visual_values: {},
  asset_values: {},
  visual_error: null,
  visual_png: null,
  has_previous_visual: false,
  zernio_post_id: "6a695286ead2fabfa56f3c27",
  revision: 3,
  lineage: { hook: null, structure: null, visual: null },
};

function publication(overrides: Partial<Publication> = {}): Publication {
  return {
    id: 1,
    draft_id: 555,
    draft_revision: 3,
    action: "publish_now",
    requested_local_time: null,
    timezone: null,
    scheduled_utc: null,
    state: "accepted",
    attempts: 1,
    last_error: null,
    created_at: "2026-08-05T10:15:00",
    accepted_at: "2026-08-05T10:15:02",
    ...overrides,
  };
}

/** A fetch stub answering every call with one body, and recording what was sent. */
function stubApi(status = 200, body: unknown = publication()) {
  const fetchStub = vi.fn(async (_url: string, _init: RequestInit) =>
    new Response(JSON.stringify(body), {
      status,
      headers: { "Content-Type": "application/json" },
    }),
  );
  vi.stubGlobal("fetch", fetchStub);
  return fetchStub;
}

/** The JSON body of the nth request the stub received. */
function sentBody(fetchStub: ReturnType<typeof stubApi>, call = 0): unknown {
  const init = fetchStub.mock.calls[call][1] as RequestInit;
  return JSON.parse(init.body as string);
}

/** The configured, switched-on destination. Overridden per test for the states that matter. */
const TARGET: PublishingTarget = {
  enabled: true,
  platform: "linkedin",
  account_id: "69719547c955c6705a96f1ce",
};

function panel(props: Partial<React.ComponentProps<typeof PublishPanel>> = {}) {
  return (
    <PublishPanel
      draft={DRAFT}
      publications={[]}
      publishing={TARGET}
      onDraft={() => {}}
      {...props}
    />
  );
}

/** The confirmation dialog, once it is open. */
function dialog(): HTMLElement {
  return screen.getByRole("dialog");
}

describe("resolveUtc — the instant the confirmation promises", () => {
  /* This is the number ADR 0002 requires the reviewer to see before firing, and it is the one
   * value on the screen that is computed rather than echoed. Every case below is a wall clock
   * that is a different instant depending on a rule nobody holds in their head. */

  it("applies a fixed offset", () => {
    // Asia/Kolkata is UTC+5:30 and has no DST — the half-hour is what a naive
    // hours-only implementation gets wrong.
    expect(resolveUtc("2026-08-12T09:00", "Asia/Kolkata")?.toISOString()).toBe(
      "2026-08-12T03:30:00.000Z",
    );
  });

  it("applies the offset in force on the day, not a fixed one", () => {
    // The same wall clock in the same zone, six months apart. A single-pass or fixed-offset
    // resolver returns the same UTC hour for both, and a schedule then goes out an hour early
    // or an hour late for half the year with nothing on screen saying so.
    expect(resolveUtc("2026-07-01T12:00", "America/New_York")?.toISOString()).toBe(
      "2026-07-01T16:00:00.000Z",
    );
    expect(resolveUtc("2026-01-01T12:00", "America/New_York")?.toISOString()).toBe(
      "2026-01-01T17:00:00.000Z",
    );
  });

  it("resolves a wall clock on the far side of a DST jump", () => {
    // 2026-03-08 02:00 local is when New York springs forward. 03:30 that morning is already
    // on the new offset, which is exactly the hour a one-pass fixed point lands wrong on.
    expect(resolveUtc("2026-03-08T03:30", "America/New_York")?.toISOString()).toBe(
      "2026-03-08T07:30:00.000Z",
    );
  });

  it("returns null for a name that is not a zone", () => {
    // `Asia/Kolkta` — the typo `distribution.resolve`'s docstring records as having come back
    // as a 500. Here it has to be a value, not an exception, or the panel crashes on a
    // half-typed zone name.
    expect(resolveUtc("2026-08-12T09:00", "Asia/Kolkta")).toBeNull();
    expect(resolveUtc("", "Asia/Kolkata")).toBeNull();
  });
});

describe("timestamps are never re-parsed", () => {
  it("renders a naive API timestamp as the UTC instant it is", () => {
    // `new Date("2026-08-05T10:15:00")` is *local* in JavaScript. Every datetime column here is
    // `timestamp without time zone`, so parsing would shift every stamp on the panel by the
    // reader's own offset and then label the result UTC.
    expect(stamp("2026-08-05T10:15:00")).toBe("2026-08-05 10:15 UTC");
  });

  it("prints — for a timestamp that was never written", () => {
    // `accepted_at` is NULL forever on a refusal. "Accepted at midnight" is a thing that did
    // not happen.
    expect(stamp(null)).toBe("—");
  });

  it("labels a resolved instant as UTC and not in the reader's zone", () => {
    expect(utcLabel(new Date("2026-08-12T03:30:00.000Z"))).toBe("2026-08-12 03:30 UTC");
  });
});

describe("classify — each refusal says something different", () => {
  const http = (status: number, message: string, detail?: unknown): ApiFailure => ({
    ok: false,
    kind: "http",
    status,
    message,
    detail,
  });

  it("says the capability is off, not that something broke", () => {
    const error = classify(
      http(403, "publishing is disabled. Set PUBLISHING_ENABLED=true to allow schedule, publish and cancel commands.", "publishing is disabled."),
      "publish_now",
    );
    expect(error.title).toMatch(/turned off/i);
    expect(error.body).toMatch(/PUBLISHING_ENABLED/);
    // Nothing to reload. A kill switch is not a stale draft.
    expect(error.currentRevision).toBeNull();
  });

  it("tells the two 409s apart, and only one of them offers a reload", () => {
    /* Both arrive as status 409 and only the object one can be fixed by looking again. This is
     * the assertion that fails the moment the discrimination is driven off the message text
     * instead of the parsed `detail`. */
    const stale = classify(
      http(409, "this draft is now at revision 5; the command was confirmed against revision 3. Reload and confirm again., current_revision: 5", {
        error: "this draft is now at revision 5; the command was confirmed against revision 3. Reload and confirm again.",
        current_revision: 5,
      }),
      "publish_now",
    );
    const never = classify(
      http(409, "draft 555 has not been pushed to Zernio, so there is no post to publish now.", "draft 555 has not been pushed to Zernio, so there is no post to publish now."),
      "publish_now",
    );

    expect(stale.currentRevision).toBe(5);
    expect(stale.title).toMatch(/changed/i);
    expect(never.currentRevision).toBeNull();
    expect(never.title).toMatch(/not in Zernio/i);
    expect(stale.title).not.toBe(never.title);
  });

  it("keeps 409, 422 and 502 as three different things", () => {
    /* The mutation this exists for is the tempting one: one `if (!result.ok)` branch with one
     * sentence in it. Every status below is a different action for the operator — reload, retype
     * the time, read the provider's reason — so a shared message is a screen that cannot tell
     * them which. */
    const conflict = classify(http(409, "draft 555 has not been pushed to Zernio.", "draft 555 has not been pushed to Zernio."), "schedule");
    const unprocessable = classify(http(422, "2026-01-01T09:00:00 Asia/Kolkata is in the past", "2026-01-01T09:00:00 Asia/Kolkata is in the past"), "schedule");
    const refused = classify(http(502, "Zernio 400: scheduledFor must be at least 5 minutes ahead", "Zernio 400: scheduledFor must be at least 5 minutes ahead"), "schedule");

    const titles = [conflict.title, unprocessable.title, refused.title];
    expect(new Set(titles).size).toBe(3);
    // Zernio's own words, surfaced rather than replaced — they are the only record of why.
    expect(refused.body).toContain("scheduledFor must be at least 5 minutes ahead");
    expect(unprocessable.body).toContain("is in the past");
  });

  it("says nothing was sent when the request never arrived", () => {
    const error = classify({ ok: false, kind: "network", message: "fetch failed" }, "schedule");
    expect(error.body).toMatch(/never arrived/i);
    expect(error.currentRevision).toBeNull();
  });
});

describe("the confirmation step", () => {
  it("fires nothing when the action is chosen", async () => {
    /* The single most important assertion in this file. ADR 0002 leans on this screen existing;
     * a button that publishes on the first click is precisely the thing it is meant to prevent,
     * and a component that skips the dialog renders a Publish button that looks identical. */
    const fetchStub = stubApi();
    render(panel());

    fireEvent.click(screen.getByRole("button", { name: /^publish now…$/i }));

    expect(fetchStub).not.toHaveBeenCalled();
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
  });

  it("shows the action, the account, both times and the zone before it fires", () => {
    render(panel());

    fireEvent.change(screen.getByLabelText(/local time/i), {
      target: { value: "2026-08-12T09:00" },
    });
    fireEvent.change(screen.getByLabelText(/timezone/i), { target: { value: "Asia/Kolkata" } });
    fireEvent.click(screen.getByRole("button", { name: /^schedule…$/i }));

    const confirm = dialog();
    expect(within(confirm).getByText("Schedule")).toBeInTheDocument();
    expect(within(confirm).getByText(/69719547c955c6705a96f1ce/)).toBeInTheDocument();
    // The local time as typed, the zone name, and the instant they resolve to — three separate
    // rows. The instant is the one a reviewer cannot work out for themselves.
    expect(within(confirm).getByText("2026-08-12 09:00")).toBeInTheDocument();
    expect(within(confirm).getByText("Asia/Kolkata")).toBeInTheDocument();
    expect(within(confirm).getByText("2026-08-12 03:30 UTC")).toBeInTheDocument();
  });

  it("presents the account as Zernio's id and not as a person", () => {
    /* The substitution this guards is `settings.voice_account` — "Monte Desai" — which is a
     * real human-readable name sitting one import away and describing something else entirely
     * (whose writing templates may copy). A confirmation is the one screen where a plausible
     * wrong answer is worse than an ugly right one. */
    render(panel());
    fireEvent.click(screen.getByRole("button", { name: /^publish now…$/i }));

    const confirm = dialog();
    expect(within(confirm).getByText(/linkedin · 69719547c955c6705a96f1ce/)).toBeInTheDocument();
    expect(within(confirm).getByText(/own account id, not a display name/i)).toBeInTheDocument();
  });

  it("prints — for the account when the destination could not be read", () => {
    render(panel({ publishing: null }));
    fireEvent.click(screen.getByRole("button", { name: /^publish now…$/i }));

    const confirm = dialog();
    expect(within(confirm).getByText(/destination could not be read/i)).toBeInTheDocument();
    // And names what it still can: the post the PUT acts on.
    expect(within(confirm).getByText(/6a695286ead2fabfa56f3c27/)).toBeInTheDocument();
  });

  it("warns when no account is configured at all", () => {
    // `account_id: null` is a real state — pushes fail — and is not the same as a failed read.
    render(panel({ publishing: { ...TARGET, account_id: null } }));
    fireEvent.click(screen.getByRole("button", { name: /^publish now…$/i }));

    expect(within(dialog()).getByText(/No LinkedIn account is configured/i)).toBeInTheDocument();
  });

  it("sends nothing when the confirmation is dismissed", async () => {
    const fetchStub = stubApi();
    render(panel());

    fireEvent.click(screen.getByRole("button", { name: /^publish now…$/i }));
    fireEvent.click(within(dialog()).getByRole("button", { name: /^cancel$/i }));

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(fetchStub).not.toHaveBeenCalled();
  });
});

describe("what goes on the wire", () => {
  it("sends the revision on a publish", async () => {
    /* Asserted on the request body, not on the screen. `revision` is required and has no
     * default: a component that drops it renders identically and the command becomes one the
     * server cannot check against the words that were read. */
    const fetchStub = stubApi();
    render(panel());

    fireEvent.click(screen.getByRole("button", { name: /^publish now…$/i }));
    fireEvent.click(within(dialog()).getByRole("button", { name: /confirm publish now/i }));

    await waitFor(() => expect(fetchStub).toHaveBeenCalledTimes(1));
    expect(fetchStub.mock.calls[0][0]).toBe("http://localhost:8000/drafts/555/publish");
    expect(sentBody(fetchStub)).toEqual({ revision: 3 });
  });

  it("sends the revision on a cancel", async () => {
    const fetchStub = stubApi();
    render(panel());

    fireEvent.click(screen.getByRole("button", { name: /^cancel schedule…$/i }));
    fireEvent.click(within(dialog()).getByRole("button", { name: /confirm cancel schedule/i }));

    await waitFor(() => expect(fetchStub).toHaveBeenCalledTimes(1));
    expect(fetchStub.mock.calls[0][0]).toBe("http://localhost:8000/drafts/555/cancel-schedule");
    expect(sentBody(fetchStub)).toEqual({ revision: 3 });
  });

  it("sends the local time as typed, the zone, and the revision on a schedule", async () => {
    const fetchStub = stubApi();
    render(panel());

    fireEvent.change(screen.getByLabelText(/local time/i), {
      target: { value: "2026-08-12T09:00" },
    });
    fireEvent.change(screen.getByLabelText(/timezone/i), { target: { value: "Asia/Kolkata" } });
    fireEvent.click(screen.getByRole("button", { name: /^schedule…$/i }));
    fireEvent.click(within(dialog()).getByRole("button", { name: /confirm schedule/i }));

    await waitFor(() => expect(fetchStub).toHaveBeenCalledTimes(1));
    expect(fetchStub.mock.calls[0][0]).toBe("http://localhost:8000/drafts/555/schedule");
    // Naive, as typed, with the zone beside it — never an offset-bearing string, which would
    // let the caller and the zone disagree with nothing raising.
    expect(sentBody(fetchStub)).toEqual({
      revision: 3,
      local_time: "2026-08-12T09:00",
      timezone: "Asia/Kolkata",
    });
  });

  it("sends the revision the confirmation was opened against, not a later one", async () => {
    /* The screen's promise is "the version you just read". Reading `draft.revision` again when
     * Confirm is pressed would hand the server whatever the draft had become in between — the
     * stale-command guard quietly answering the question it exists to ask. */
    const fetchStub = stubApi();
    const { rerender } = render(panel());

    fireEvent.click(screen.getByRole("button", { name: /^publish now…$/i }));
    // The draft moves under the open dialog.
    rerender(panel({ draft: { ...DRAFT, revision: 9 } }));
    fireEvent.click(within(dialog()).getByRole("button", { name: /confirm publish now/i }));

    await waitFor(() => expect(fetchStub).toHaveBeenCalledTimes(1));
    expect(sentBody(fetchStub)).toEqual({ revision: 3 });
  });

  it("sends the opened-against revision on a schedule too", async () => {
    /* The same assertion on the other branch of the body. Both are worth having: `fire` builds
     * the schedule body and the confirm body separately, so a fix applied to one and not the
     * other leaves the more consequential path — the one that puts a post on a clock weeks
     * out — reading the revision at fire time with nothing failing. */
    const fetchStub = stubApi();
    const { rerender } = render(panel());

    fireEvent.change(screen.getByLabelText(/local time/i), {
      target: { value: "2026-08-12T09:00" },
    });
    fireEvent.change(screen.getByLabelText(/timezone/i), { target: { value: "Asia/Kolkata" } });
    fireEvent.click(screen.getByRole("button", { name: /^schedule…$/i }));
    rerender(panel({ draft: { ...DRAFT, revision: 9 } }));
    fireEvent.click(within(dialog()).getByRole("button", { name: /confirm schedule/i }));

    await waitFor(() => expect(fetchStub).toHaveBeenCalledTimes(1));
    expect(sentBody(fetchStub)).toEqual({
      revision: 3,
      local_time: "2026-08-12T09:00",
      timezone: "Asia/Kolkata",
    });
  });
});

describe("refusals reach the operator in their own words", () => {
  it("offers a reload for a stale revision and names the number", async () => {
    const fetchStub = stubApi(409, {
      detail: {
        error: "this draft is now at revision 5; the command was confirmed against revision 3. Reload and confirm again.",
        current_revision: 5,
      },
    });
    render(panel());

    fireEvent.click(screen.getByRole("button", { name: /^publish now…$/i }));
    fireEvent.click(within(dialog()).getByRole("button", { name: /confirm publish now/i }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/changed while you were reading it/i);
    expect(within(alert).getByRole("button", { name: /reload this draft \(now at revision 5\)/i })).toBeInTheDocument();
    expect(fetchStub).toHaveBeenCalledTimes(1);
  });

  it("does not offer a reload for a draft that was never pushed", async () => {
    stubApi(409, { detail: "draft 555 has not been pushed to Zernio, so there is no post to publish now." });
    render(panel());

    fireEvent.click(screen.getByRole("button", { name: /^publish now…$/i }));
    fireEvent.click(within(dialog()).getByRole("button", { name: /confirm publish now/i }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/not in Zernio/i);
    // Reloading fixes nothing here, and offering it would send the operator round a loop.
    expect(within(alert).queryByRole("button", { name: /reload/i })).not.toBeInTheDocument();
  });

  it("shows Zernio's own refusal text on a 502", async () => {
    stubApi(502, { detail: "Zernio 400: scheduledFor must be at least 5 minutes ahead" });
    render(panel());

    fireEvent.click(screen.getByRole("button", { name: /^publish now…$/i }));
    fireEvent.click(within(dialog()).getByRole("button", { name: /confirm publish now/i }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/Zernio refused/i);
    expect(alert).toHaveTextContent(/at least 5 minutes ahead/);
    expect(alert).not.toHaveTextContent(/changed while you were reading it/i);
  });

  it("says the capability is off on a 403 and does not call it a fault", async () => {
    stubApi(403, {
      detail: "publishing is disabled. Set PUBLISHING_ENABLED=true to allow schedule, publish and cancel commands.",
    });
    render(panel());

    fireEvent.click(screen.getByRole("button", { name: /^publish now…$/i }));
    fireEvent.click(within(dialog()).getByRole("button", { name: /confirm publish now/i }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/turned off/i);
    expect(alert).toHaveTextContent(/PUBLISHING_ENABLED/);
    expect(within(alert).queryByRole("button", { name: /reload/i })).not.toBeInTheDocument();
  });

  it("reloads the draft and hands it back when the reload is taken", async () => {
    const seen: Draft[] = [];
    const fetchStub = vi.fn(async (url: string, _init: RequestInit) =>
      url.endsWith("/publish")
        ? new Response(JSON.stringify({ detail: { error: "stale", current_revision: 5 } }), {
            status: 409,
            headers: { "Content-Type": "application/json" },
          })
        : new Response(JSON.stringify({ ...DRAFT, revision: 5 }), {
            status: 200,
            headers: { "Content-Type": "application/json" },
          }),
    );
    vi.stubGlobal("fetch", fetchStub);
    render(panel({ onDraft: (d) => seen.push(d) }));

    fireEvent.click(screen.getByRole("button", { name: /^publish now…$/i }));
    fireEvent.click(within(dialog()).getByRole("button", { name: /confirm publish now/i }));
    fireEvent.click(await screen.findByRole("button", { name: /reload this draft/i }));

    await waitFor(() => expect(seen).toHaveLength(1));
    expect(seen[0].revision).toBe(5);
  });
});

describe("the history beside the button", () => {
  it("distinguishes a failed read from an empty history", () => {
    /* A Publish button rendered over "nothing has been commanded" when the read actually failed
     * is how one post gets commanded twice — the route's own docstring says so. */
    render(panel({ publications: null }));

    expect(screen.getByText(/could not be read/i)).toBeInTheDocument();
    expect(screen.queryByText(/Nothing has been commanded/i)).not.toBeInTheDocument();
  });

  it("says so plainly when nothing has been commanded", () => {
    render(panel({ publications: [] }));
    expect(screen.getByText(/Nothing has been commanded/i)).toBeInTheDocument();
  });

  it("prints — for the times an action never named, and 0 for an attempt count of zero", () => {
    /* Both directions of the same rule. The three time columns are NULL for `publish_now`
     * because no time was requested — an absence. `attempts: 0` on a `requested` row is a
     * measurement: it says the command was written down and never sent, which is the one state
     * an operator most needs to see, and rendering it as `—` would hide it. */
    render(
      panel({
        publications: [publication({ state: "requested", attempts: 0, accepted_at: null })],
      }),
    );

    const row = screen.getByRole("listitem");
    expect(within(row).getByText("Requested for").nextElementSibling).toHaveTextContent("—");
    expect(within(row).getByText("Resolved UTC").nextElementSibling).toHaveTextContent("—");
    expect(within(row).getByText("Attempts").nextElementSibling).toHaveTextContent("0");
    expect(within(row).getByText("Accepted").nextElementSibling).toHaveTextContent("—");
  });

  it("shows all three forms of the time for a schedule", () => {
    render(
      panel({
        publications: [
          publication({
            action: "schedule",
            requested_local_time: "2026-08-12T09:00:00",
            timezone: "Asia/Kolkata",
            scheduled_utc: "2026-08-12T03:30:00",
          }),
        ],
      }),
    );

    const row = screen.getByRole("listitem");
    expect(within(row).getByText("Requested for").nextElementSibling).toHaveTextContent(
      "2026-08-12 09:00 Asia/Kolkata",
    );
    expect(within(row).getByText("Resolved UTC").nextElementSibling).toHaveTextContent(
      "2026-08-12 03:30 UTC",
    );
  });

  it("shows the provider's refusal text on a failed command", () => {
    render(
      panel({
        publications: [
          publication({ state: "failed", last_error: "Zernio 400: account not connected", accepted_at: null }),
        ],
      }),
    );
    expect(screen.getByText(/account not connected/)).toBeInTheDocument();
  });

  it("adds the command it just issued to the history", async () => {
    stubApi(200, publication({ id: 42, state: "accepted" }));
    render(panel({ publications: [] }));

    fireEvent.click(screen.getByRole("button", { name: /^publish now…$/i }));
    fireEvent.click(within(dialog()).getByRole("button", { name: /confirm publish now/i }));

    // Not for cosmetics: the history is what stops a second command, so a command that does not
    // appear in it is a Publish button standing over an out-of-date list of what has been done.
    await waitFor(() => expect(screen.getAllByRole("listitem")).toHaveLength(1));
    expect(screen.getByText("accepted")).toBeInTheDocument();
  });

  it("does not draw a second row when the same command is repeated", async () => {
    // A repeat of a command that already reached a conclusion answers with the row it already
    // has. Two rows for one publication would overstate what happened.
    stubApi(200, publication({ id: 42 }));
    render(panel({ publications: [] }));

    for (let press = 0; press < 2; press += 1) {
      fireEvent.click(screen.getByRole("button", { name: /^publish now…$/i }));
      fireEvent.click(within(dialog()).getByRole("button", { name: /confirm publish now/i }));
      await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    }

    expect(screen.getAllByRole("listitem")).toHaveLength(1);
  });
});

describe("the timezone field", () => {
  it("refuses to offer a schedule it cannot resolve", () => {
    render(panel());

    fireEvent.change(screen.getByLabelText(/local time/i), {
      target: { value: "2026-08-12T09:00" },
    });
    fireEvent.change(screen.getByLabelText(/timezone/i), { target: { value: "Asia/Kolkta" } });

    expect(screen.getByText(/not a timezone name this browser knows/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^schedule…$/i })).toBeDisabled();
  });

  it("warns that an instant already past will be refused", () => {
    render(panel());

    fireEvent.change(screen.getByLabelText(/local time/i), {
      target: { value: "2020-01-01T09:00" },
    });
    fireEvent.change(screen.getByLabelText(/timezone/i), { target: { value: "Asia/Kolkata" } });
    fireEvent.click(screen.getByRole("button", { name: /^schedule…$/i }));

    expect(within(dialog()).getByText(/already passed/i)).toBeInTheDocument();
  });
});

describe("the kill switch, before a command is composed", () => {
  it("says the switch is off and disables all three commands", async () => {
    /* Without the read, the only way to discover the capability is off is to fire a command
     * and read the 403 — finding out by attempting the thing. The 403 branch stays: this is a
     * read taken when the page rendered and the server is still what decides. */
    const fetchStub = stubApi();
    render(panel({ publishing: { ...TARGET, enabled: false } }));

    expect(screen.getByText(/Publishing is switched off/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^publish now…$/i })).toBeDisabled();
    expect(screen.getByRole("button", { name: /^cancel schedule…$/i })).toBeDisabled();
    expect(screen.getByRole("button", { name: /^schedule…$/i })).toBeDisabled();

    fireEvent.click(screen.getByRole("button", { name: /^publish now…$/i }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(fetchStub).not.toHaveBeenCalled();
  });

  it("leaves the commands enabled when the switch could not be read", () => {
    /* An unread flag is not "off". Disabling on a request that did not arrive would hide a
     * capability that is working, which is the same conflation as rendering a failed read of
     * the history as an empty history. */
    render(panel({ publishing: null }));

    expect(screen.getByText(/publishing target could not be read/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^publish now…$/i })).toBeEnabled();
    expect(screen.queryByText(/Publishing is switched off/i)).not.toBeInTheDocument();
  });

  it("says nothing about the switch when it is on", () => {
    render(panel());
    expect(screen.queryByText(/switched off/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/could not be read/i)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^publish now…$/i })).toBeEnabled();
  });
});
