// @vitest-environment happy-dom
import { afterEach, expect, test } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import ReadingWorkspace from "./ReadingWorkspace.tsx";
import { respondWith } from "@/test/responses.ts";
afterEach(cleanup);

const publishedBook = {
  id: "book",
  title: "材料",
  state: "ready",
  run_id: "run",
  stage: "published",
  created_at: "2026-11-05",
  revision: 1,
  revision_pending: false,
  pending_count: 0,
  navigation_pending_count: 0,
};
function publishedReading(initial: string) {
  const chapters = ["first", "second"].map((id, position) => ({
    id,
    title: position === 0 ? "第一章" : "第二章",
    book_id: "book",
    run_id: "run",
    position,
    pages: [position + 1],
    codepoints: 5,
    text: position === 0 ? "起初阅读的正文。" : "检索命中的正文。",
    text_sha256: "a".repeat(64),
    parts: [],
  }));
  respondWith((request) => {
    const path = new URL(request.url).pathname;
    if (path.endsWith("/reading-navigation"))
      return { entries: [], state: "not_detected", version: 0, sha256: "" };
    if (path === "/api/v2/search")
      return [
        {
          id: "hit",
          book_id: "book",
          chapter_id: "second",
          run_id: "run",
          title: "第二章",
          text: "检索命中",
          pages: [2],
          context: [],
          reading_target: {
            chapter_id: "second",
            run_id: "run",
            physical_page: 2,
            start: 0,
            end: 4,
            text_sha256: "a".repeat(64),
            anchor: "",
            image_sha256: "",
          },
        },
      ];
    if (path.endsWith("/chapters")) return chapters;
    return chapters.find((chapter) => path.endsWith(chapter.id));
  });
  render(
    <MemoryRouter initialEntries={[initial]}>
      <QueryClientProvider
        client={
          new QueryClient({ defaultOptions: { queries: { retry: false } } })
        }
      >
        <ReadingWorkspace book={publishedBook} />
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

test("search stays beside the current chapter and restores its query after a cross-chapter visit", async () => {
  publishedReading("/books/book?tab=reader&chapter=first");
  await screen.findByText("起初阅读的正文。");
  const article = document.querySelector(".platform-reading-columns > article");
  await userEvent.click(screen.getByRole("button", { name: "检索本书" }));
  expect(
    screen.getByRole("complementary", { name: "本书检索侧栏" }),
  ).toBeTruthy();
  expect(document.querySelector(".platform-reading-columns > article")).toBe(
    article,
  );
  await userEvent.type(screen.getByLabelText("检索本书正文"), "粮食");
  await userEvent.click(screen.getByRole("button", { name: "查找" }));
  await userEvent.click(
    await screen.findByRole("link", { name: "阅读命中段落" }),
  );
  await screen.findByLabelText("正文定位位置");
  expect(
    document.querySelector(".platform-reading-columns > article")?.textContent,
  ).toContain("检索命中的正文。");
  expect(
    screen.queryByRole("complementary", { name: "本书检索侧栏" }),
  ).toBeNull();
  await userEvent.click(screen.getByRole("button", { name: "上一章" }));
  await screen.findByText("起初阅读的正文。");
  await userEvent.click(screen.getByRole("link", { name: "返回检索结果" }));
  await screen.findByRole("link", { name: "阅读命中段落" });
  expect(screen.getByLabelText("检索本书正文")).toHaveProperty("value", "粮食");
  expect(screen.getByText("起初阅读的正文。")).toBeTruthy();
  await userEvent.click(screen.getByRole("button", { name: "关闭检索" }));
  expect(screen.queryByLabelText("检索本书正文")).toBeNull();
  expect(screen.getByText("起初阅读的正文。")).toBeTruthy();
});

test.each(["tab=search", "tab=reader&section=search"])(
  "direct search route %s retains the requested chapter",
  async (section) => {
    publishedReading(`/books/book?${section}&chapter=second&q=粮食`);
    await screen.findByText("检索命中的正文。");
    expect(screen.queryByText("起初阅读的正文。")).toBeNull();
    expect(
      screen.getByRole("complementary", { name: "本书检索侧栏" }),
    ).toBeTruthy();
    expect(screen.getByLabelText("检索本书正文")).toHaveProperty(
      "value",
      "粮食",
    );
  },
);
test("unpublished search explains availability and takes the reader directly to the retained draft", async () => {
  const calls: string[] = [];
  respondWith((request) => {
    calls.push(request.url);
    return request.url.includes("/structure")
      ? { items: [] }
      : {
          page: 1,
          page_count: 1,
          machine_status: "unreviewed-source",
          text: "保留的识别底稿",
          image: "/original.png",
          footnotes: [],
        };
  });
  render(
    <MemoryRouter initialEntries={["/?tab=search"]}>
      <QueryClientProvider client={new QueryClient()}>
        <ReadingWorkspace
          book={{
            id: "book",
            title: "材料",
            state: "running",
            run_id: "run",
            stage: "vision",
            created_at: "2026-11-05",
            revision: 1,
            revision_pending: false,
            pending_count: 1,
            navigation_pending_count: 0,
          }}
        />
      </QueryClientProvider>
    </MemoryRouter>,
  );
  expect(
    screen.getByRole("heading", { name: "本书尚未建立检索索引" }),
  ).toBeDefined();
  expect(screen.queryByRole("button", { name: "查找" })).toBeNull();
  expect(calls.some((url) => url.includes("/search"))).toBe(false);
  await userEvent.click(screen.getByRole("button", { name: "先阅读底稿" }));
  await screen.findByText("保留的识别底稿");
  expect(
    screen.getByRole("link", { name: "去核对 1 页正文" }).getAttribute("href"),
  ).toBe("#/books/book?tab=review");
});
