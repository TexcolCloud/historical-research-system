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
test("a missing directory page opens metadata work even when no discovered entry is pending", async () => {
  respondWith(() => ({
    id: "book",
    title: "材料",
    state: "ready",
    pending_count: 0,
    navigation_pending_count: 0,
    navigation_missing_page_count: 1,
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
  await screen.findByText("目录确认工作区");
  expect(
    screen.getByText("目录仍有 1 页待补全。当前目录清单尚不代表全部条目。"),
  ).toBeDefined();
  expect(
    screen.getByRole("button", { name: "目录确认 · 0 项 · 补全 1 页" }),
  ).toBeDefined();
  expect(screen.queryByText("正文终审工作区")).toBeNull();
});
test("stopped books never claim automatic processing is still running", async () => {
  respondWith(() => ({
    id: "book",
    title: "材料",
    state: "failed",
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
  await screen.findByText("自动处理已停止，请在处理进展中从断点恢复。");
  expect(
    screen.queryByText("系统按页自动处理，已有底稿可在阅读资料中查看。"),
  ).toBeNull();
});
test("book workspaces expose both human task types and research cards without making users navigate process stages", async () => {
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
  ).toHaveLength(4);
  expect(screen.getByRole("tab", { name: "史料卡" })).toBeDefined();
  expect(screen.getByRole("tab", { name: /阅读资料/ })).toBeDefined();
  await userEvent.click(
    screen.getByRole("button", { name: "目录确认 · 2 项" }),
  );
  await screen.findByText("目录确认工作区");
});
