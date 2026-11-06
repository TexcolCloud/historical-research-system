// @vitest-environment happy-dom
import { expect, test, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import RetryRunButton from "@/features/runs/RetryRunButton.tsx";
import { client } from "@/client/api.ts";
test("a lost retry response reuses the same request identity", async () => {
  const post = vi.spyOn(client, "POST").mockResolvedValue({
    error: { detail: [] },
    response: new Response(null, { status: 503 }),
  });
  render(
    <QueryClientProvider client={new QueryClient()}>
      <RetryRunButton runId="run" />
    </QueryClientProvider>,
  );
  await userEvent.click(screen.getByRole("button", { name: "重试本阶段" }));
  await screen.findByRole("alert");
  await userEvent.click(screen.getByRole("button", { name: "重试本阶段" }));
  expect(post.mock.calls[0]).toEqual(post.mock.calls[1]);
  vi.restoreAllMocks();
});
