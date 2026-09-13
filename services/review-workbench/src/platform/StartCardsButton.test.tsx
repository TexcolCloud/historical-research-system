// @vitest-environment happy-dom
import { expect, test, vi } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import StartCardsButton from "./StartCardsButton";
import { client } from "./client";
test("starting cards cannot double-submit while the receipt is uncertain", async () => {
  const post = vi.spyOn(client, "POST").mockResolvedValue({
    error: { detail: [] },
    response: new Response(null, { status: 503 }),
  });
  render(
    <QueryClientProvider client={new QueryClient()}>
      <StartCardsButton bookId="book" />
    </QueryClientProvider>,
  );
  await userEvent.click(screen.getByRole("button", { name: "新建制卡任务" }));
  await screen.findByRole("alert");
  await userEvent.click(screen.getByRole("button", { name: "新建制卡任务" }));
  expect(post.mock.calls[0]).toEqual(post.mock.calls[1]);
  cleanup();
  vi.restoreAllMocks();
});
