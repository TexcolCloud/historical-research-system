// @vitest-environment happy-dom
import { afterEach, expect, test, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import NavigationPanel from "./NavigationPanel.tsx";
import { RuntimeScopeContext } from "@/app/runtime-scope.ts";
import { respondWith } from "@/test/responses.ts";
import { client } from "@/client/api.ts";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const candidate = {
  physical_page: 5,
  printed_label: "4",
  anchor: "第一章",
  start: 0,
  markdown_sha256: "a".repeat(64),
  image_sha256: "b".repeat(64),
  sequence: "body",
};
const recommended = {
  id: "recommended",
  title: "第一章",
  level: 1,
  path: ["第一章"],
  source_page: 1,
  source_anchor: "第一章 3",
  source_image: "/toc.png",
  target_label: "3",
  status: "conflict",
  needs_confirmation: true,
  reason: "待核对",
  candidates: [candidate],
};
const missing = {
  ...recommended,
  id: "missing",
  title: "待定位文章",
  status: "not_found",
  candidates: [],
};
const report = {
  state: "available",
  version: 1,
  sha256: "c".repeat(64),
  coverage_complete: true,
  pending_count: 2,
  entries: [recommended, missing],
  approved_pages: [
    { page: 5, headings: ["第一章"], printed_label: "4", image: "/p5.png" },
  ],
};

function mount(value = report) {
  const requests: string[] = [];
  respondWith((request) => {
    requests.push(new URL(request.url).pathname);
    return request.url.includes("review-pages")
      ? {
          page: 5,
          page_count: 5,
          text: "# 第一章\n正文开头。",
          image: "/p5.png",
          footnotes: [],
        }
      : value;
  });
  const query = new QueryClient();
  render(
    <QueryClientProvider client={query}>
      <RuntimeScopeContext.Provider value="content">
        <NavigationPanel runId="run" bookId="book" reviewOnly />
      </RuntimeScopeContext.Provider>
    </QueryClientProvider>,
  );
  return { query, requests };
}

test("human directory work groups recommended matches separately from entries needing a search", async () => {
  mount();
  await screen.findByText("正文开头。");
  await userEvent.click(screen.getByRole("button", { name: "需查找正文 · 1" }));
  expect(screen.getByRole("searchbox", { name: "查找正文标题" })).toBeDefined();
  expect(screen.queryByRole("button", { name: "选择目录：第一章" })).toBeNull();
  expect(
    screen.getByRole("button", { name: "选择目录：待定位文章" }),
  ).toBeDefined();
});

test("the next pending directory candidate is prefetched before the user switches entries", async () => {
  const { requests } = mount({
    ...report,
    entries: [
      recommended,
      {
        ...recommended,
        id: "next",
        title: "下一篇",
        candidates: [{ ...candidate, physical_page: 8 }],
      },
    ],
  });
  await screen.findByText("正文开头。");
  await waitFor(() =>
    expect(requests).toContain("/api/v2/runs/run/review-pages/8"),
  );
});

test("updating recommendations invokes matching without submitting a human decision", async () => {
  const post = vi.spyOn(client, "POST").mockResolvedValue({
    data: { ...report, version: 2, sha256: "d".repeat(64) },
    response: new Response(),
  });
  mount();
  await screen.findByText("正文开头。");
  await userEvent.click(screen.getByRole("button", { name: "更新推荐" }));
  await screen.findByText(/目录推荐已更新/);
  expect(post).toHaveBeenCalledExactlyOnceWith(
    "/api/v2/runs/{run_id}/navigation/refresh",
    { params: { path: { run_id: "run" } } },
  );
});

test("partial recommendation assistance reports its warning beside the successful base matching result", async () => {
  vi.spyOn(client, "POST").mockResolvedValue({
    data: {
      ...report,
      version: 2,
      sha256: "d".repeat(64),
      assistance_warning: "语义检索暂不可用，已保留文字匹配推荐。",
    },
    response: new Response(),
  });
  mount();
  await screen.findByText("正文开头。");
  await userEvent.click(screen.getByRole("button", { name: "更新推荐" }));
  const status =
    await screen.findByText(/语义检索暂不可用，已保留文字匹配推荐/);
  expect(status.getAttribute("role")).toBe("status");
});

test("finishing the selected group invites a group switch instead of claiming another entry was opened", async () => {
  vi.spyOn(client, "POST").mockResolvedValue({
    data: {
      ...report,
      version: 2,
      sha256: "d".repeat(64),
      pending_count: 1,
      entries: [
        {
          ...recommended,
          status: "matched_manual",
          needs_confirmation: false,
          target: candidate,
        },
        missing,
      ],
    },
    response: new Response(),
  });
  mount();
  await screen.findByText("正文开头。");
  await userEvent.click(screen.getByRole("button", { name: "有推荐候选 · 1" }));
  await userEvent.click(
    screen.getByRole("button", { name: "确认跳转到 PDF 第 5 页" }),
  );
  await screen.findByText(/当前组已完成，可切换其他分组/);
  expect(screen.queryByText(/已打开下一项目录/)).toBeNull();
});
