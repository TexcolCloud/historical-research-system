// @vitest-environment happy-dom
// @vitest-environment-options {"settings":{"disableIframePageLoading":true}}
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { act, cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import ReaderPanel from "@/features/library/ReaderPanel.tsx";
import { respondWith } from "@/test/responses.ts";
import { readingNavigationOptions } from "./navigation-query";
import { research } from "@/features/research/api";
beforeEach(() =>
  vi.spyOn(research, "reading").mockResolvedValue({
    chapter_id: "",
    run_id: "",
    text_sha256: "",
    cards: [],
    annotations: [],
  }),
);
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
    request.url.includes("/reading-navigation")
      ? { entries: [], state: "not_detected", version: 0, sha256: "" }
      : request.url.includes("/review-pages/")
        ? { page: 7, image: "/original-7.png" }
        : new URL(request.url).pathname.includes("/books/")
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
  expect(
    screen.getByText("结构整理结果").closest(".reader-toolbar"),
  ).not.toBeNull();
  await userEvent.click(screen.getByText("结构整理结果"));
  expect(
    await screen.findByText("暂无可展示的结构关系，仍按原文阅读。"),
  ).toBeTruthy();
  expect(
    screen.getByText("0 项可用于连续阅读 · 0 项暂按原页显示"),
  ).toBeTruthy();
  await userEvent.click(screen.getByText("结构整理结果"));
  expect(screen.queryByTitle("原书 PDF")).toBeNull();
  await userEvent.click(screen.getByRole("button", { name: "对照原书" }));
  expect((await screen.findByAltText("原书第 7 页")).getAttribute("src")).toBe(
    "/original-7.png",
  );
  expect(
    screen.getByRole("link", { name: "打开原 PDF" }).getAttribute("href"),
  ).toContain("original.pdf#page=7");
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
    if (path.endsWith("/reading-navigation"))
      return { entries: [], state: "not_detected", version: 0, sha256: "" };
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

test.each([
  ["资料编者按。\n\n# **第一章**\n\n正文保留。", undefined],
  ["旧候选", "资料编者按。\n\n第一章\n======\n\n正文保留。"],
])(
  "a source heading supplies the title without a duplicate metadata h1",
  async (text, readingText) => {
    const chapter = {
      id: "chapter",
      book_id: "book",
      run_id: "run",
      position: 0,
      title: "第一章",
      kind: "article",
      pages: [1],
      codepoints: text.length,
      text,
      reading_text: readingText,
      parts: [],
    };
    respondWith((request) =>
      request.url.includes("reading-navigation")
        ? { entries: [], state: "not_detected", version: 0, sha256: "" }
        : request.url.includes("/books/")
          ? [chapter]
          : chapter,
    );
    render(
      <MemoryRouter>
        <QueryClientProvider client={new QueryClient()}>
          <ReaderPanel bookId="book" />
        </QueryClientProvider>
      </MemoryRouter>,
    );
    await screen.findByText("正文保留。");
    expect(
      screen.getAllByRole("heading", { level: 1, name: "第一章" }),
    ).toHaveLength(1);
    expect(
      document.querySelector(".platform-reading .prose h1")?.textContent,
    ).toBe("第一章");
  },
);

test("a title inside a code block does not suppress the chapter heading", async () => {
  const chapter = {
    id: "chapter",
    book_id: "book",
    run_id: "run",
    position: 0,
    title: "第一章",
    kind: "article",
    pages: [1],
    codepoints: 30,
    text: "```md\n# 第一章\n```\n\n正文保留。",
    parts: [],
  };
  respondWith((request) =>
    request.url.includes("reading-navigation")
      ? { entries: [], state: "not_detected", version: 0, sha256: "" }
      : request.url.includes("/books/")
        ? [chapter]
        : chapter,
  );
  render(
    <MemoryRouter>
      <QueryClientProvider client={new QueryClient()}>
        <ReaderPanel bookId="book" />
      </QueryClientProvider>
    </MemoryRouter>,
  );
  await screen.findByText("正文保留。");
  expect(
    screen.getAllByRole("heading", { level: 1, name: "第一章" }),
  ).toHaveLength(1);
  expect(document.querySelector(".platform-reading .prose h1")).toBeNull();
});

test("title comparison ignores only footnote references and preserves their rendered links", async () => {
  const text =
    "资料编者按。\n\n# 第一章[原文链接](https://example.org/source)[^*]\n\n正文保留。\n\n[^*]: 标题说明。";
  const referenceAt = text.indexOf("[^*]");
  const noteAt = text.lastIndexOf("[^*]");
  const chapter = {
    id: "chapter",
    book_id: "book",
    run_id: "run",
    position: 0,
    title: "第一章原文链接",
    kind: "article",
    pages: [1],
    codepoints: text.length,
    text,
    parts: [],
    footnotes: [
      {
        id: "title-note",
        label: "*",
        note: {
          start: noteAt,
          end: text.length,
          marker_end: noteAt + "[^*]:".length,
        },
        references: [{ start: referenceAt, end: referenceAt + "[^*]".length }],
      },
    ],
  };
  respondWith((request) =>
    request.url.includes("reading-navigation")
      ? { entries: [], state: "not_detected", version: 0, sha256: "" }
      : request.url.includes("/books/")
        ? [chapter]
        : chapter,
  );
  render(
    <MemoryRouter>
      <QueryClientProvider client={new QueryClient()}>
        <ReaderPanel bookId="book" />
      </QueryClientProvider>
    </MemoryRouter>,
  );
  await screen.findByText("正文保留。");
  expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
  const heading = document.querySelector(".platform-reading .prose h1");
  const reference = screen.getByRole("link", { name: "查看脚注 *" });
  expect(heading?.contains(reference)).toBe(true);
  expect(reference.classList.contains("footnote-reference")).toBe(true);
  expect(reference.getAttribute("data-note-jump")).not.toBeNull();
  expect(
    screen.getByRole("link", { name: "原文链接" }).getAttribute("href"),
  ).toBe("https://example.org/source");
  const target = document.getElementById(
    reference.getAttribute("href")!.slice(1),
  );
  expect(target?.classList.contains("footnote-target")).toBe(true);
  expect(
    screen.getByRole("link", { name: "返回正文注号 * 1" }).getAttribute("href"),
  ).toBe(`#${reference.id}`);
});

test.each([
  [[1, 2, 3, 4, 5, 6, 7, 8, 9, 10], "PDF 第 1–10 页"],
  [[1, 3, 5, 7, 9], "PDF 第 1、3、5 等 5 页"],
])(
  "source pages stay complete behind a compact provenance summary",
  async (pages, label) => {
    const chapter = {
      id: "chapter",
      book_id: "book",
      run_id: "run",
      position: 0,
      title: "第一章",
      kind: "article",
      pages,
      codepoints: 5,
      text: "正文保留。",
      parts: [],
    };
    respondWith((request) =>
      request.url.includes("reading-navigation")
        ? { entries: [], state: "not_detected", version: 0, sha256: "" }
        : request.url.includes("/books/")
          ? [chapter]
          : chapter,
    );
    render(
      <MemoryRouter>
        <QueryClientProvider client={new QueryClient()}>
          <ReaderPanel bookId="book" />
        </QueryClientProvider>
      </MemoryRouter>,
    );
    await screen.findByText("正文保留。");
    const summary = screen.getByText(label);
    expect(summary.closest("details")?.open).toBe(false);
    await userEvent.click(summary);
    expect(summary.closest("details")?.open).toBe(true);
    expect(screen.getByText(`完整 PDF 页码：${pages.join("、")}`)).toBeTruthy();
  },
);

test("an absent source directory defaults to chapters without overriding a later manual choice", async () => {
  const chapter = {
    id: "chapter",
    book_id: "book",
    run_id: "run",
    position: 0,
    title: "第一章",
    kind: "article",
    pages: [1],
    codepoints: 5,
    text: "正文保留。",
    parts: [],
  };
  const query = new QueryClient();
  respondWith((request) =>
    request.url.includes("reading-navigation")
      ? { entries: [], state: "not_detected", version: 0, sha256: "" }
      : request.url.includes("/books/")
        ? [chapter]
        : chapter,
  );
  render(
    <MemoryRouter>
      <QueryClientProvider client={query}>
        <ReaderPanel bookId="book" />
      </QueryClientProvider>
    </MemoryRouter>,
  );
  await screen.findByText("正文保留。");
  const chapters = screen.getByRole("button", { name: "章节", pressed: true });
  await userEvent.click(chapters);
  await act(async () => {
    query.setQueryData(readingNavigationOptions("book").queryKey, {
      entries: [
        {
          id: "toc",
          title: "新增目录",
          path: ["新增目录"],
          level: 1,
          source_page: 1,
          source_image: "/page.png",
          source_anchor: "新增目录 1",
          target_label: "1",
          status: "not_found",
          reason: "待确认",
          needs_confirmation: true,
        },
      ],
      state: "available",
      version: 1,
      sha256: "",
      pending_count: 1,
      coverage_complete: true,
    });
  });
  expect(
    screen.getByRole("button", { name: "章节", pressed: true }),
  ).toBeTruthy();
  await userEvent.click(screen.getByRole("button", { name: "原书目录" }));
  expect(screen.getByText("新增目录")).toBeTruthy();
});
