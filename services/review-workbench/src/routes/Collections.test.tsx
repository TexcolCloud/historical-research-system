// @vitest-environment happy-dom
import { afterEach, expect, test, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { respondWith } from "@/test/responses.ts";
import CardCollection from "@/routes/CardsPage.tsx";
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
test("card collection separates adopted output from unverified candidates and retains exact book/card navigation", async () => {
  respondWith((request) =>
    new URL(request.url).pathname === "/api/v2/cards"
      ? [
          {
            id: "accepted",
            book_id: "book",
            title: "运输记录",
            state: "adopted",
          },
          {
            id: "candidate",
            book_id: "book",
            title: "待修候选",
            state: "needs_revision",
          },
        ]
      : [{ id: "book", title: "研究书籍", state: "ready" }],
  );
  show(<CardCollection />);
  expect(
    (await screen.findByRole("link", { name: /运输记录/ })).getAttribute(
      "href",
    ),
  ).toBe("/books/book?tab=cards&card=accepted");
  expect(screen.queryByText("待修候选")).toBeNull();
  await userEvent.click(screen.getByRole("tab", { name: "机器核验未通过" }));
  expect(screen.getByText("待修候选")).toBeTruthy();
});
