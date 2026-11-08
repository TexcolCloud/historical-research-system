// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import ReviewsPage from "./ReviewsPage.tsx";
import { respondWith } from "@/test/responses.ts";
import { research } from "@/features/research/api";
beforeEach(() =>
  vi
    .spyOn(research, "pending")
    .mockResolvedValue({ items: [], total: 0, offset: 0, limit: 50 }),
);
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

test("running content issues and independent directory conflicts stay discoverable", async () => {
  respondWith(() => [
    {
      id: "body",
      title: "仍在识别的书",
      state: "running",
      pending_count: 2,
      navigation_pending_count: 0,
    },
    {
      id: "nav",
      title: "已发布的书",
      state: "ready",
      pending_count: 0,
      navigation_pending_count: 1,
    },
  ]);
  render(
    <MemoryRouter>
      <QueryClientProvider client={new QueryClient()}>
        <ReviewsPage />
      </QueryClientProvider>
    </MemoryRouter>,
  );
  expect(
    (await screen.findByRole("link", { name: /仍在识别的书/ })).getAttribute(
      "href",
    ),
  ).toBe("/books/body?tab=review");
  expect(
    screen.getByRole("link", { name: /已发布的书/ }).getAttribute("href"),
  ).toBe("/books/nav?tab=review&task=navigation");
  expect(screen.queryByText("暂时没有需要处理的内容")).toBeNull();
});
