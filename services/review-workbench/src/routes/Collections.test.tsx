// @vitest-environment happy-dom
import { afterEach, expect, test, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { respondWith } from "@/test/responses.ts";
import ReviewInbox from "@/routes/ReviewsPage.tsx";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});
function show(view: React.ReactNode) {
  render(
    <MemoryRouter>
      <QueryClientProvider
        client={
          new QueryClient({ defaultOptions: { queries: { retry: false } } })
        }
      >
        {view}
      </QueryClientProvider>
    </MemoryRouter>,
  );
}
test("inbox links pending books to their review and failed books to recovery without releasing content", async () => {
  respondWith(() => [
    { id: "pending", title: "待核对书", state: "awaiting_review" },
    { id: "failed", title: "失败书", state: "failed" },
    { id: "ready", title: "可读书", state: "ready" },
  ]);
  show(<ReviewInbox />);
  expect(
    (await screen.findByRole("link", { name: /待核对书/ })).getAttribute(
      "href",
    ),
  ).toBe("/books/pending?tab=review");
  expect(
    screen.getByRole("link", { name: /失败书/ }).getAttribute("href"),
  ).toBe("/books/failed?tab=progress");
  expect(screen.queryByText("可读书")).toBeNull();
});
