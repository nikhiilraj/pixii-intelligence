import "@testing-library/jest-dom/vitest";

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { RouteError } from "./route-error";

/* The body of all seven `error.tsx` files. What is ours here — and therefore what is tested —
 * is what the boundary *says* and what its retry *does*. That Next mounts an `error.tsx` on a
 * throw is the framework's job and is not re-asserted; it was exercised against a real
 * production build instead (see the slice report).
 *
 * `useRouter` is mocked because this component is rendered here outside an App Router tree,
 * where the real hook throws. The mock is also what makes the retry assertable: the retry has
 * to do BOTH things, and `reset()` alone is the plausible mistake — it re-renders the segment
 * against the RSC payload it already has, so on a server-component route it can reproduce the
 * same failure forever without ever asking the server again.
 */
const refresh = vi.hoisted(() => vi.fn());
vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh }) }));

afterEach(() => {
  refresh.mockClear();
  cleanup();
});

function boom(message: string, digest?: string): Error & { digest?: string } {
  return Object.assign(new Error(message), digest ? { digest } : {});
}

describe("a route's error boundary", () => {
  it("names the route and reports the error's own message", () => {
    render(<RouteError what="The scoreboard" error={boom("a row arrived with no version")} reset={vi.fn()} />);

    expect(
      screen.getByRole("heading", { name: "The scoreboard could not be shown" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("a row arrived with no version");
  });

  /* What a production build actually hands over. Verified against a real `next build` + `next
     start`: a server component that throws reaches the client with its message replaced and
     only a digest surviving — the probe's message string was absent from the entire response
     while its digest `3045073859` appeared twice. So the digest is rendered when there is one,
     and an empty message says so rather than rendering blank. */
  it("falls back to the digest when the message has been stripped", () => {
    render(<RouteError what="Studio" error={boom("", "3045073859")} reset={vi.fn()} />);

    expect(screen.getByRole("alert")).toHaveTextContent("The error arrived without a message.");
    expect(screen.getByRole("alert")).toHaveTextContent("digest 3045073859");
  });

  it("does not invent a digest when there is none", () => {
    render(<RouteError what="Studio" error={boom("something threw")} reset={vi.fn()} />);

    expect(screen.queryByText(/digest/)).not.toBeInTheDocument();
  });

  it("retries by re-requesting as well as re-rendering", () => {
    const reset = vi.fn();
    render(<RouteError what="The corpus" error={boom("something threw")} reset={reset} />);

    fireEvent.click(screen.getByRole("button", { name: "Try again" }));

    // Both, and `refresh` is the load-bearing one: it is what goes back to the server.
    expect(refresh).toHaveBeenCalledTimes(1);
    expect(reset).toHaveBeenCalledTimes(1);
  });
});
