// @vitest-environment happy-dom
import { afterEach, expect, test, vi } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import BookPage from "./BookPage.tsx";
import { respondWith } from "@/test/responses.ts";
vi.mock("@/features/review/ReviewPanel.tsx", () => ({
  default: () => <p>正文终审工作区</p>,
}));
vi.mock("@/features/library/NavigationPanel.tsx", () => ({
  default: () => <p>目录确认工作区</p>,
}));
afterEach(cleanup);
test("three workspaces expose both human task types without making users navigate process stages", async () => {
  respondWith(() => ({
    id: "book",
    title: "材料",
    state: "running",
    pending_count: 1,
    navigation_pending_count: 2,
    run_id: "run",
    revision: 1,
  }));
  render(
    <MemoryRouter initialEntries={["/books/book?tab=review"]}>
      <QueryClientProvider client={new QueryClient()}>
        <Routes>
          <Route path="/books/:bookId" element={<BookPage />} />
        </Routes>
      </QueryClientProvider>
    </MemoryRouter>,
  );
  await screen.findByText("正文终审工作区");
  expect(
    within(screen.getByRole("tablist", { name: "书籍工作区" })).getAllByRole(
      "tab",
    ),
  ).toHaveLength(3);
  expect(screen.getByRole("tab", { name: /阅读资料/ })).toBeDefined();
  await userEvent.click(
    screen.getByRole("button", { name: "目录确认 · 2 项" }),
  );
  await screen.findByText("目录确认工作区");
});
