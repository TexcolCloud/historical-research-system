// @vitest-environment happy-dom
// @vitest-environment-options {"settings":{"disableIframePageLoading":true}}
import { afterEach, expect, test, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import ReaderPanel from "./ReaderPanel";
import { respondWith } from "./test-responses";
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});
test("reads a complete logical chapter first and opens only its associated original pages on demand", async () => {
  const chapter = {
    id: "chapter",
    book_id: "book",
    run_id: "run",
    position: 0,
    title: "第一章",
    kind: "article",
    pages: [7, 8],
    codepoints: 8,
  };
  respondWith((request) =>
    new URL(request.url).pathname.includes("/books/")
      ? [chapter]
      : { ...chapter, text: "连续的章节正文", parts: [] },
  );
  render(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter>
        <ReaderPanel bookId="book" />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  await screen.findByText("连续的章节正文");
  expect(screen.queryByTitle("原书 PDF")).toBeNull();
  await userEvent.click(screen.getByRole("button", { name: "对照原书" }));
  expect(screen.getByTitle("原书 PDF").getAttribute("src")).toContain(
    "original.pdf#page=7",
  );
  vi.restoreAllMocks();
});

test("a search link opens its chapter instead of the first chapter", async () => {
  const entries = ["first", "matched"].map((id, position) => ({
    id,
    position,
    book_id: "book",
    run_id: "run",
    title: id,
    kind: "article",
    pages: [position + 1],
    codepoints: 10,
  }));
  respondWith((request) => {
    const path = new URL(request.url).pathname;
    if (path.includes("/books/")) return entries;
    const entry = entries.find((item) => path.endsWith(item.id))!;
    return { ...entry, text: `${entry.id}正文`, parts: [] };
  });
  render(
    <MemoryRouter initialEntries={["/books/book?tab=reader&chapter=matched"]}>
      <QueryClientProvider client={new QueryClient()}>
        <ReaderPanel bookId="book" />
      </QueryClientProvider>
    </MemoryRouter>,
  );
  await screen.findByText("matched正文");
  expect(screen.queryByText("first正文")).toBeNull();
});
