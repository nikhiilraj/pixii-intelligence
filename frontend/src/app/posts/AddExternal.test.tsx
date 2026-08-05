import "@testing-library/jest-dom/vitest";

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import AddExternal from "./AddExternal";

const refresh = vi.hoisted(() => vi.fn());
const toastError = vi.hoisted(() => vi.fn());

vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh }) }));
vi.mock("sonner", () => ({ toast: { error: toastError } }));

function response(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
  refresh.mockClear();
  toastError.mockClear();
  cleanup();
});

function openForm() {
  render(<AddExternal />);
  fireEvent.click(screen.getByRole("button", { name: "Add a post from elsewhere" }));
}

it("declares engagement as a non-negative whole-number count", () => {
  openForm();

  expect(screen.getByPlaceholderText("engaged actions")).toHaveAttribute("type", "number");
  expect(screen.getByPlaceholderText("engaged actions")).toHaveAttribute("min", "0");
  expect(screen.getByPlaceholderText("engaged actions")).toHaveAttribute("step", "1");
});

it("adds a manual post, clears the form and refreshes the corpus", async () => {
  const fetchStub = vi.fn((_input: RequestInfo | URL, _init?: RequestInit) =>
    Promise.resolve(response(201, { id: 41 })),
  );
  vi.stubGlobal("fetch", fetchStub);
  openForm();

  fireEvent.change(screen.getByPlaceholderText("Paste the post text…"), {
    target: { value: "A post Zernio does not carry." },
  });
  fireEvent.change(screen.getByPlaceholderText("who wrote it (optional)"), {
    target: { value: "A creator" },
  });
  fireEvent.change(screen.getByPlaceholderText("engaged actions"), {
    target: { value: "17" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Add to corpus" }));

  await waitFor(() => expect(fetchStub).toHaveBeenCalledTimes(1));
  expect(JSON.parse(String(fetchStub.mock.calls[0][1]?.body))).toEqual({
    content: "A post Zernio does not carry.",
    author: "A creator",
    engaged_actions: 17,
  });
  await waitFor(() => expect(refresh).toHaveBeenCalledTimes(1));
  expect(screen.getByRole("button", { name: "Add a post from elsewhere" })).toBeInTheDocument();
});

it("keeps the entered post available to retry when the API refuses it", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(() => Promise.resolve(response(422, { detail: "engaged_actions must be non-negative" }))),
  );
  openForm();

  const content = screen.getByPlaceholderText("Paste the post text…");
  fireEvent.change(content, { target: { value: "Keep this text for the retry." } });
  fireEvent.click(screen.getByRole("button", { name: "Add to corpus" }));

  await waitFor(() =>
    expect(toastError).toHaveBeenCalledWith("Could not add the post", {
      description: "engaged_actions must be non-negative",
    }),
  );
  expect(content).toHaveValue("Keep this text for the retry.");
  expect(refresh).not.toHaveBeenCalled();
});
