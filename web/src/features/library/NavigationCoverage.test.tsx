// @vitest-environment happy-dom
import { afterEach, expect, test, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import NavigationPanel from "./NavigationPanel";
import NavigationSearch from "./NavigationSearch";
import NavigationReview from "./NavigationReview";
import userEvent from "@testing-library/user-event";
import { RuntimeScopeContext } from "@/app/runtime-scope";
import { respondWith } from "@/test/responses";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

test("an omitted directory page stays visible even when every discovered entry is linked", async () => {
  respondWith(() => ({
    state: "available",
    entries: [],
    version: 1,
    sha256: "a".repeat(64),
    pending_count: 0,
    pages_complete: true,
    coverage_complete: false,
    missing_pages: [10],
    unresolved_pages: [],
    approved_pages: [{ page: 10, headings: [], image: "/page10.png" }],
  }));
  render(
    <QueryClientProvider client={new QueryClient()}>
      <RuntimeScopeContext.Provider value="content">
        <NavigationPanel runId="run" bookId="book" reviewOnly />
      </RuntimeScopeContext.Provider>
    </QueryClientProvider>,
  );
  expect(await screen.findByText("目录尚未补全")).toBeDefined();
  expect(
    screen
      .getByRole("link", { name: "查看 PDF 第 10 页原图" })
      .getAttribute("href"),
  ).toBe("/page10.png");
  expect(screen.queryByText("当前没有需要你确认的目录项目。")).toBeNull();
});

test("full cross-page titles are searchable while the real first fragment remains the target", async () => {
  respondWith(() => ({
    page: 7,
    text: "关于甲乙\n12",
    image: "/p7.png",
    footnotes: [],
  }));
  const choose = vi.fn();
  render(
    <QueryClientProvider client={new QueryClient()}>
      <NavigationSearch
        title="关于甲乙工作的决定"
        runId="run"
        busy={false}
        onChoose={choose}
        pages={[
          {
            page: 7,
            headings: ["关于甲乙"],
            titles: [
              {
                physical_page: 7,
                sequence: "body",
                title: "关于甲乙工作的决定",
                anchor: "关于甲乙",
                start: 0,
                markdown_sha256: "a".repeat(64),
                image_sha256: "b".repeat(64),
                title_spans: [
                  {
                    page: 7,
                    anchor: "关于甲乙",
                    start: 0,
                    end: 4,
                    markdown_sha256: "a".repeat(64),
                    image_sha256: "b".repeat(64),
                  },
                  {
                    page: 8,
                    anchor: "工作的决定",
                    start: 0,
                    end: 5,
                    markdown_sha256: "c".repeat(64),
                    image_sha256: "d".repeat(64),
                  },
                ],
              },
            ],
          },
        ]}
      />
    </QueryClientProvider>,
  );
  expect(await screen.findByText("关于甲乙工作的决定")).toBeDefined();
  expect(screen.getByText("找到 1 个正文标题。")).toBeDefined();
});

test("cross-page comparison shows every source and continuation image and submits their evidence", async () => {
  const first = {
    page: 7,
    anchor: "关于甲乙",
    start: 0,
    end: 4,
    markdown_sha256: "a".repeat(64),
    image_sha256: "b".repeat(64),
    image: "/p7.png",
  };
  const second = {
    page: 8,
    anchor: "工作的决定",
    start: 0,
    end: 5,
    markdown_sha256: "c".repeat(64),
    image_sha256: "d".repeat(64),
    image: "/p8.png",
  };
  const candidate = {
    physical_page: 7,
    sequence: "body",
    anchor: first.anchor,
    start: 0,
    markdown_sha256: first.markdown_sha256,
    image_sha256: first.image_sha256,
    title: "关于甲乙工作的决定",
    title_spans: [first, second],
  };
  const entry = {
    id: "e",
    title: candidate.title,
    level: 1,
    path: [candidate.title],
    source_page: 1,
    source_anchor: "关于甲乙",
    source_image: "/p1.png",
    source_spans: [
      { ...first, page: 1, image: "/p1.png" },
      { ...second, page: 2, image: "/p2.png" },
    ],
    target_label: "12",
    status: "conflict",
    needs_confirmation: true,
    reason: "跨页",
    candidates: [candidate],
  };
  respondWith((request) => ({
    page: Number(new URL(request.url).pathname.split("/").at(-1)),
    text: request.url.endsWith("/8") ? "工作的决定\n正文。" : "关于甲乙\n12",
    image: request.url.endsWith("/8") ? "/p8.png" : "/p7.png",
    footnotes: [],
  }));
  const decide = vi.fn();
  render(
    <QueryClientProvider client={new QueryClient()}>
      <NavigationReview
        entry={entry}
        report={{
          state: "available",
          pending_count: 1,
          coverage_complete: true,
          entries: [entry],
          version: 1,
          sha256: "a".repeat(64),
          approved_pages: [
            { page: 7, headings: [first.anchor], image: "/p7.png" },
            { page: 8, headings: [second.anchor], image: "/p8.png" },
          ],
        }}
        runId="run"
        busy={false}
        onDecide={decide}
        onCancel={() => undefined}
      />
    </QueryClientProvider>,
  );
  expect(
    await screen.findByRole("img", { name: "目录原图 · PDF 第 2 页" }),
  ).toBeDefined();
  expect(
    screen.getByRole("img", { name: "标题续页原图 · PDF 第 8 页" }),
  ).toBeDefined();
  await userEvent.click(
    await screen.findByRole("button", { name: "确认跳转到 PDF 第 7 页" }),
  );
  expect(decide).toHaveBeenCalledWith(
    expect.objectContaining({
      title: candidate.title,
      title_spans: [first, second],
    }),
    "a".repeat(64),
  );
});
