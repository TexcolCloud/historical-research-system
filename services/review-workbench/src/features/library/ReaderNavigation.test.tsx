// @vitest-environment happy-dom
import { afterEach, expect, test, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import ReaderPanel from "./ReaderPanel";
import { readingHref } from "./reader-location";
import { respondWith } from "@/test/responses";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});
const sha = "a".repeat(64);
const tocText = "# 目录\n\n同名 …… 3\n\n同名 …… 4\n";
const bodyText = "# 同名\n\n第一段。\n\n# 同名\n\n第二段。\n";
const bodyStart = bodyText.lastIndexOf("同名");
const target = {
  chapter_id: "body",
  run_id: "run",
  physical_page: 7,
  start: bodyStart,
  end: bodyStart + 2,
  text_sha256: sha,
  anchor: "同名",
  image_sha256: sha,
};
function Url() {
  return <output aria-label="地址">{useLocation().search}</output>;
}
function setup(initial = "/books/book?tab=reader&chapter=toc") {
  const chapters = [
    {
      id: "toc",
      title: "原书目录",
      position: 0,
      pages: [2],
      text: tocText,
      toc_links: [
        {
          id: "entry",
          title: "第二篇",
          level: 1,
          source_start: tocText.lastIndexOf("同名"),
          source_end: tocText.lastIndexOf("同名") + "同名 …… 4".length,
          target,
        },
      ],
    },
    {
      id: "body",
      title: "正文合集",
      position: 1,
      pages: [7],
      text: bodyText,
      toc_links: [],
    },
  ].map((c) => ({
    ...c,
    book_id: "book",
    run_id: "run",
    kind: "article",
    codepoints: c.text.length,
    text_sha256: sha,
    parts: [],
    reading_segments: [],
    reading_text: c.text,
  }));
  respondWith((request) => {
    const path = new URL(request.url).pathname;
    if (path.endsWith("/reading-navigation"))
      return {
        entries: [
          {
            id: "entry",
            title: "第二篇",
            level: 1,
            path: ["第二篇"],
            source_page: 2,
            source_anchor: "同名 …… 4",
            source_image: "/toc.png",
            target_label: "4",
            status: "matched",
            reason: "",
            reading_target: target,
          },
        ],
        state: "ready",
        version: 1,
        sha256: sha,
      };
    if (path.includes("/review-pages/")) return { image: "/page-7.png" };
    if (path.includes("/books/")) return chapters;
    return chapters.find((c) => path.endsWith(c.id));
  });
  render(
    <MemoryRouter initialEntries={[initial]}>
      <QueryClientProvider
        client={
          new QueryClient({ defaultOptions: { queries: { retry: false } } })
        }
      >
        <ReaderPanel bookId="book" />
        <Url />
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

test("inline original TOC entry reaches the second duplicate heading and can return", async () => {
  setup();
  const toc = await screen.findByRole("link", { name: "阅读第二篇" });
  await userEvent.click(toc);
  await screen.findByText("第二段。");
  const marks = document.querySelectorAll("mark[data-reading-focus]");
  expect(marks).toHaveLength(1);
  expect(marks[0].textContent).toBe("同名");
  expect(marks[0].closest("h1")?.nextElementSibling?.textContent).toBe(
    "第二段。",
  );
  await userEvent.click(screen.getByRole("link", { name: "返回目录" }));
  await screen.findByRole("link", { name: "阅读第二篇" });
});

test("local chapter changes never override a later source-bound URL", async () => {
  setup();
  await userEvent.click(
    await screen.findByRole("button", { name: /正文合集/ }),
  );
  await screen.findByText("第二段。");
  await userEvent.click(screen.getByRole("link", { name: /第二篇.*书内页/ }));
  expect(document.querySelector("mark[data-reading-focus] h1")).toBeNull();
  expect((await screen.findByLabelText("正文定位位置")).textContent).toBe(
    "同名",
  );
  expect(screen.getByLabelText("地址").textContent).toContain("start=");
});

test("an outdated target exposes the original instead of highlighting another publication", async () => {
  setup(readingHref("book", { ...target, text_sha256: "c".repeat(64) }));
  await screen.findByText(/定位依据已更新|无法精确对应/);
  expect(document.querySelectorAll("mark[data-reading-focus]")).toHaveLength(0);
  expect(await screen.findByAltText("原书第 7 页")).toBeTruthy();
});

test.each([
  "/books/book?tab=reader&section=search&q=%E4%BA%BA%E7%89%A9",
  "/books/book?tab=search&q=%E4%BA%BA%E7%89%A9",
])(
  "a search reading link labels its unchanged return route accurately: %s",
  async (returnTo) => {
    setup(readingHref("book", target, { returnTo }));
    const link = await screen.findByRole("link", { name: "返回检索结果" });
    expect(link.getAttribute("href")).toBe(returnTo);
    expect(screen.queryByRole("link", { name: "返回目录" })).toBeNull();
  },
);
