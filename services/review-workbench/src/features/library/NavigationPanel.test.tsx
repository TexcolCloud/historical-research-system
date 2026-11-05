// @vitest-environment happy-dom
import { afterEach, expect, test, vi } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import NavigationPanel from "./NavigationPanel.tsx";
import { client } from "@/client/api.ts";
import { respondWith } from "@/test/responses.ts";
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});
test("published reading directory uses its frozen report and source-bound text link", async () => {
  const requests: string[] = [];
  respondWith((request) => {
    requests.push(new URL(request.url).pathname);
    return {
      state: "available",
      version: 1,
      sha256: "a".repeat(64),
      coverage_complete: true,
      entries: [
        {
          id: "entry",
          title: "章节乙",
          level: 1,
          path: ["章节乙"],
          source_page: 1,
          source_anchor: "章节乙 4",
          source_image: "/toc.png",
          target_label: "4",
          status: "matched",
          reason: "",
          target: {
            physical_page: 7,
            start: 2,
            anchor: "章节乙",
            markdown_sha256: "a".repeat(64),
            image_sha256: "b".repeat(64),
          },
          reading_target: {
            chapter_id: "chapter",
            run_id: "run",
            physical_page: 7,
            start: 10,
            end: 13,
            text_sha256: "c".repeat(64),
            anchor: "章节乙",
            image_sha256: "b".repeat(64),
          },
        },
      ],
      approved_pages: [],
    };
  });
  render(
    <QueryClientProvider client={new QueryClient()}>
      <NavigationPanel
        runId="new-run"
        bookId="book"
        published
        bookState="awaiting_review"
      />
    </QueryClientProvider>,
  );
  const link = await screen.findByRole("link", { name: "阅读正文 →" });
  expect(requests).toEqual(["/api/v2/books/book/reading-navigation"]);
  expect(link.getAttribute("href")).toContain("section=text");
  expect(link.getAttribute("href")).toContain("start=10");
  expect(screen.queryByRole("button", { name: "修改跳转" })).toBeNull();
  expect(
    screen.getByRole("link", { name: "核对目录对应位置" }).getAttribute("href"),
  ).toContain("task=navigation");
});

test("an unlinked published entry still reaches its existing individual human mapping form", async () => {
  respondWith(() => ({
    state: "available",
    version: 1,
    sha256: "a".repeat(64),
    coverage_complete: true,
    entries: [
      {
        id: "missing-entry",
        title: "未找到的条目",
        level: 1,
        path: ["未找到的条目"],
        source_page: 1,
        source_anchor: "未找到的条目 9",
        source_image: "/toc.png",
        target_label: "9",
        status: "not_found",
        reason: "未找到对应正文",
        target: null,
        reading_target: null,
      },
    ],
    approved_pages: [],
  }));
  render(
    <QueryClientProvider client={new QueryClient()}>
      <NavigationPanel runId="run" bookId="book" published bookState="ready" />
    </QueryClientProvider>,
  );
  const item = (await screen.findByText("未找到的条目")).closest("li")!;
  const link = within(item).getByRole("link", { name: "核对这条目录" });
  expect(link.getAttribute("href")).toBe(
    "#/books/book?tab=review&task=navigation&entry=missing-entry",
  );
  expect(within(item).queryByRole("link", { name: "阅读正文 →" })).toBeNull();
  expect(within(item).queryByRole("button", { name: "修改跳转" })).toBeNull();
});
test("an incomplete directory does not claim processing is running for a paused book", async () => {
  respondWith(() => ({
    state: "not_detected",
    version: 1,
    sha256: "",
    entries: [],
    approved_pages: [],
    coverage_complete: false,
  }));
  render(
    <QueryClientProvider client={new QueryClient()}>
      <NavigationPanel runId="run" bookId="book" bookState="paused" />
    </QueryClientProvider>,
  );
  await screen.findByText(
    "自动处理已暂停。请在处理进展中继续处理，新的目录项目将在核对后列出。",
  );
  expect(
    screen.queryByText("其他页面仍在自动核验，新发现的问题会继续出现在这里。"),
  ).toBeNull();
});

test("a changed evidence version reports beside the action and reloads before retrying", async () => {
  const target = {
    physical_page: 2,
    anchor: "起源",
    printed_label: "1",
    start: 2,
    markdown_sha256: "b".repeat(64),
    image_sha256: "c".repeat(64),
  };
  const entry = {
    id: "stale",
    title: "起源",
    level: 1,
    path: ["起源"],
    source_page: 1,
    source_anchor: "起源 1",
    source_image: "/toc.png",
    target_label: "1",
    status: "conflict",
    reason: "需要核对",
    candidates: [target],
  };
  let sha = "a".repeat(64);
  const getReport = () => ({
    state: "available",
    version: 1,
    sha256: sha,
    coverage_complete: true,
    entries: [entry],
    approved_pages: [{ page: 2, headings: ["起源"], image: "/body.png" }],
  });
  respondWith((request) =>
    request.url.includes("review-pages")
      ? {
          page: 2,
          page_count: 2,
          text: "# 起源\n正文。",
          image: "/body.png",
          footnotes: [],
        }
      : getReport(),
  );
  const post = vi
    .spyOn(client, "POST")
    .mockResolvedValueOnce({ response: new Response(null, { status: 409 }) })
    .mockImplementation(async () => ({
      data: {
        ...getReport(),
        entries: [{ ...entry, status: "matched_manual", target }],
      },
      response: new Response(),
    }));
  render(
    <QueryClientProvider client={new QueryClient()}>
      <NavigationPanel runId="run" bookId="book" reviewOnly />
    </QueryClientProvider>,
  );
  await screen.findByText("正文。");
  await userEvent.click(
    screen.getByRole("button", { name: "就是这篇，确认跳转" }),
  );
  const alert = await screen.findByRole("alert");
  expect(
    within(screen.getByRole("group", { name: "目录裁决操作" })).getByRole(
      "alert",
    ),
  ).toBe(alert);
  sha = "d".repeat(64);
  await userEvent.click(
    screen.getByRole("button", { name: "重新载入核对证据" }),
  );
  await screen.findByText("正文。");
  await userEvent.click(
    screen.getByRole("button", { name: "就是这篇，确认跳转" }),
  );
  expect(post).toHaveBeenLastCalledWith(
    "/api/v2/runs/{run_id}/navigation/{entry_id}/decision",
    expect.objectContaining({
      body: expect.objectContaining({ expected_sha256: sha }),
    }),
  );
  await screen.findByRole("heading", { name: "当前目录核对完成" });
});

test("human task opens the candidate evidence and confirms it without a separate selection step", async () => {
  const target = {
    physical_page: 5,
    printed_label: "4",
    anchor: "第一章",
    start: 2,
    markdown_sha256: "b".repeat(64),
    image_sha256: "c".repeat(64),
  };
  const entry = {
    id: "direct",
    title: "第一章",
    level: 1,
    path: ["第一章"],
    source_page: 1,
    source_anchor: "第一章 3",
    source_image: "/toc.png",
    target_label: "3",
    status: "conflict",
    reason: "存在重复目标或目录页码与正文不一致",
    target: null,
    candidates: [target],
  };
  const report = {
    state: "available",
    version: 1,
    sha256: "a".repeat(64),
    coverage_complete: false,
    entries: [entry],
    approved_pages: [
      { page: 5, headings: ["第一章"], printed_label: "4", image: "/body.png" },
    ],
  };
  respondWith((request) =>
    request.url.includes("review-pages")
      ? {
          page: 5,
          page_count: 5,
          text: "# 第一章\n（一九三四年一月五日）\n这是正文开头。\n4",
          image: "/body.png",
          footnotes: [],
        }
      : report,
  );
  const post = vi.spyOn(client, "POST").mockResolvedValue({
    data: {
      ...report,
      entries: [{ ...entry, status: "matched_manual", target }],
    },
    response: new Response(),
  });
  render(
    <QueryClientProvider client={new QueryClient()}>
      <NavigationPanel runId="run" bookId="book" reviewOnly />
    </QueryClientProvider>,
  );
  await screen.findByRole("heading", {
    name: "这条目录是否对应右侧这篇正文？",
  });
  await screen.findByText(/这是正文开头/);
  expect(screen.getByText(/目录记载.*3.*系统识别.*4/)).toBeDefined();
  expect(screen.queryByRole("combobox")).toBeNull();
  expect(post).not.toHaveBeenCalled();
  await userEvent.click(
    screen.getByRole("button", { name: "就是这篇，确认跳转" }),
  );
  expect(post).toHaveBeenCalledWith(
    "/api/v2/runs/{run_id}/navigation/{entry_id}/decision",
    expect.objectContaining({
      body: {
        decision_id: expect.any(String),
        expected_sha256: report.sha256,
        target_page: 5,
        target_anchor: "第一章",
      },
    }),
  );
  await screen.findByRole("heading", { name: "当前目录核对完成" });
  expect(screen.getByText(/已确认.*第一章.*PDF 第 5 页/)).toBeDefined();
});

test("a conflicting printed page stays unlinked until a source-bound navigation decision", async () => {
  const target = {
    physical_page: 3,
    printed_label: "39",
    anchor: "1936年",
    start: 2,
    markdown_sha256: "b".repeat(64),
    image_sha256: "c".repeat(64),
  };
  const report = {
    state: "available",
    version: 2,
    sha256: "a".repeat(64),
    coverage_complete: true,
    approved_pages: [{ page: 3, headings: ["1936年"], printed_label: "39" }],
    entries: [
      {
        id: "entry",
        title: "1936年",
        level: 1,
        path: ["1936年"],
        source_page: 1,
        source_anchor: "1936年 38",
        source_image: "/original-toc.png",
        target_label: "38",
        status: "conflict",
        reason: "目录与正文页码不一致",
        target: null,
        candidates: [target],
      },
    ],
  };
  respondWith((request) =>
    request.url.includes("review-pages")
      ? {
          page: 3,
          page_count: 3,
          text: "# 1936年\n正文开头。",
          image: "/body.png",
          footnotes: [],
        }
      : report,
  );
  const post = vi.spyOn(client, "POST").mockResolvedValue({
    data: {
      ...report,
      entries: [{ ...report.entries[0]!, status: "matched_manual", target }],
    },
    response: new Response(),
  });
  render(
    <QueryClientProvider client={new QueryClient()}>
      <NavigationPanel runId="run" bookId="book" />
    </QueryClientProvider>,
  );
  await screen.findByText(/需要确认目录跳转/);
  expect(screen.queryByText("阅读正文 →")).toBeNull();
  expect(
    screen.getByRole("link", { name: /目录原图/ }).getAttribute("href"),
  ).toBe("/original-toc.png");
  await userEvent.click(screen.getByRole("button", { name: "确认目录跳转" }));
  await screen.findByText("正文开头。");
  await userEvent.click(
    screen.getByRole("button", { name: "就是这篇，确认跳转" }),
  );
  expect(post).toHaveBeenCalledWith(
    "/api/v2/runs/{run_id}/navigation/{entry_id}/decision",
    expect.objectContaining({
      body: {
        decision_id: expect.any(String),
        expected_sha256: report.sha256,
        target_page: 3,
        target_anchor: "1936年",
      },
    }),
  );
});

test("confirmed directory entries can be reopened without turning a preview into another decision", async () => {
  const target = {
    physical_page: 2,
    anchor: "起源",
    printed_label: "1",
    start: 2,
    markdown_sha256: "b".repeat(64),
    image_sha256: "c".repeat(64),
  };
  const entry = {
    id: "reopen",
    title: "起源",
    level: 1,
    path: ["起源"],
    source_page: 1,
    source_anchor: "起源 1",
    source_image: "/toc.png",
    target_label: "1",
    status: "matched_manual",
    reason: "人工确认",
    target,
    candidates: [target],
  };
  const report = {
    state: "available",
    version: 1,
    sha256: "a".repeat(64),
    coverage_complete: true,
    entries: [entry],
    approved_pages: [{ page: 2, headings: ["起源"], image: "/body.png" }],
  };
  respondWith((request) =>
    request.url.includes("review-pages")
      ? {
          page: 2,
          page_count: 2,
          text: "# 起源\n正文。",
          image: "/body.png",
          footnotes: [],
        }
      : report,
  );
  const post = vi.spyOn(client, "POST").mockResolvedValue({
    data: {
      ...report,
      entries: [{ ...entry, status: "unlinked_manual", target: null }],
    },
    response: new Response(),
  });
  render(
    <QueryClientProvider client={new QueryClient()}>
      <NavigationPanel runId="run" bookId="book" />
    </QueryClientProvider>,
  );
  await userEvent.click(
    await screen.findByRole("button", { name: "修改跳转" }),
  );
  await screen.findByText("正文。");
  expect(post).not.toHaveBeenCalled();
  await userEvent.click(
    screen.getByRole("button", { name: "不是这篇，查找其他正文" }),
  );
  await userEvent.click(
    screen.getByRole("button", { name: "保留目录文字，不设置跳转" }),
  );
  expect(post).toHaveBeenCalledWith(
    "/api/v2/runs/{run_id}/navigation/{entry_id}/decision",
    expect.objectContaining({
      body: {
        decision_id: expect.any(String),
        expected_sha256: report.sha256,
        target_page: null,
        target_anchor: "",
      },
    }),
  );
  await screen.findByText("已确认不设置跳转");
});
