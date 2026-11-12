// @vitest-environment happy-dom
import { afterEach, expect, test, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { respondWith } from "@/test/responses";
import BookPage from "./BookPage";

vi.mock("@/features/runs/RunDetails.tsx", () => ({
  default: () => <p>处理记录</p>,
}));
afterEach(cleanup);

test.each([
  { state: "ready", link: "← 书籍" },
  { state: "deleted", link: "返回全部书籍" },
  { state: null, link: "返回书籍" },
])(
  "$state book navigation returns to the source library rather than the dashboard",
  async ({ state, link }) => {
    respondWith(() =>
      state
        ? { id: "book", title: "测试史料", state, created_at: "2026-11-11" }
        : null,
    );
    render(
      <MemoryRouter initialEntries={["/books/book"]}>
        <QueryClientProvider
          client={
            new QueryClient({ defaultOptions: { queries: { retry: false } } })
          }
        >
          <Routes>
            <Route path="/books/:bookId" element={<BookPage />} />
            <Route path="/library" element={<h1>史料集入口</h1>} />
            <Route path="/" element={<h1>仪表盘入口</h1>} />
          </Routes>
        </QueryClientProvider>
      </MemoryRouter>,
    );
    await userEvent.click(await screen.findByRole("link", { name: link }));
    expect(
      await screen.findByRole("heading", { name: "史料集入口" }),
    ).toBeTruthy();
    expect(screen.queryByRole("heading", { name: "仪表盘入口" })).toBeNull();
  },
);
