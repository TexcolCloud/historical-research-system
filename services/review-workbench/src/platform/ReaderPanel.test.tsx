// @vitest-environment happy-dom
// @vitest-environment-options {"settings":{"disableIframePageLoading":true}}
import { expect, test, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import ReaderPanel from "./ReaderPanel";
import { respondWith } from "./test-responses";
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
      <ReaderPanel bookId="book" />
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
