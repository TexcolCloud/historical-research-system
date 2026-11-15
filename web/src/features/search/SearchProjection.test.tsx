// @vitest-environment happy-dom
import { afterEach, expect, test, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { respondWith } from "@/test/responses";
import SearchPanel from "./SearchPanel";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function show() {
  render(
    <MemoryRouter initialEntries={["/books/book?q=正文&semantic=false"]}>
      <QueryClientProvider
        client={
          new QueryClient({ defaultOptions: { queries: { retry: false } } })
        }
      >
        <SearchPanel bookId="book" />
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

test("search displays mapped visible fragments and retains all identical-text sources", async () => {
  const target = {
    chapter_id: "chapter",
    run_id: "run",
    physical_page: 3,
    start: 5,
    end: 7,
    text_sha256: "a".repeat(64),
    anchor: "正文",
  };
  respondWith((request) =>
    request.url.includes("reading-navigation")
      ? { entries: [] }
      : [
          {
            id: "hit",
            book_id: "book",
            chapter_id: "chapter",
            run_id: "run",
            title: "标题",
            text: "不可展示的页眉正文888注释",
            snippet: "正文\n注释",
            pages: [3],
            start: 0,
            end: 20,
            score: 1,
            role: "body",
            layer: "source",
            context: [],
            snippet_segments: [
              {
                text: "正文",
                start: 5,
                end: 7,
                synthetic: false,
                usage: "primary",
              },
              {
                text: "\n",
                start: null,
                end: null,
                synthetic: true,
                usage: "primary",
              },
              {
                text: "注释",
                start: 10,
                end: 12,
                synthetic: false,
                usage: "primary",
              },
            ],
            ranges: [
              { start: 5, end: 7, usage: "primary" },
              { start: 10, end: 12, usage: "primary" },
            ],
            reading_targets: [
              target,
              { ...target, start: 10, end: 12, anchor: "注释" },
            ],
            occurrences: [
              {
                id: "a",
                book_id: "book",
                run_id: "run",
                book_title: "版本甲",
                pages: [3],
                reading_target: target,
              },
              {
                id: "b",
                book_id: "other",
                run_id: "other-run",
                book_title: "版本乙",
                pages: [9],
                reading_target: { ...target, physical_page: 9 },
              },
            ],
          },
        ],
  );
  show();
  expect(await screen.findByText("正文 注释")).toBeTruthy();
  expect(screen.queryByText(/不可展示的页眉/)).toBeNull();
  expect(
    screen.getByText("摘要由多个原文片段组成，引用前请分别核对。"),
  ).toBeTruthy();
  const second = screen.getByRole("link", { name: "片段 2 · PDF 第 3 页" });
  expect(second.getAttribute("href")).toContain("start=10");
  expect(screen.getByText("相同文字的 2 处来源")).toBeTruthy();
  await userEvent.click(screen.getByText("相同文字的 2 处来源"));
  expect(
    screen
      .getByRole("link", { name: "版本乙 · PDF 第 9 页" })
      .getAttribute("href"),
  ).toContain("/books/other");
});

test("all-original lookup explicitly applies lexical search without changing a draft option early", async () => {
  let applied = new URL("http://test");
  respondWith((request) => {
    if (request.url.includes("reading-navigation")) return { entries: [] };
    applied = new URL(request.url);
    return [];
  });
  show();
  await screen.findByText(/未找到相关内容/);
  await userEvent.click(screen.getByText("检索选项"));
  await userEvent.selectOptions(screen.getByLabelText("查找内容"), "all");
  expect(applied.searchParams.get("source_scope")).toBe("content");
  expect(screen.getByLabelText("检索方式")).toHaveProperty("disabled", true);
  await userEvent.click(screen.getByRole("button", { name: "查找" }));
  await vi.waitFor(() =>
    expect(applied.searchParams.get("source_scope")).toBe("all"),
  );
  expect(applied.searchParams.get("semantic")).toBe("false");
});
