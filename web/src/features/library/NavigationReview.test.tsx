// @vitest-environment happy-dom
import { afterEach, expect, test, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import NavigationReview from "./NavigationReview.tsx";
import { respondWith } from "@/test/responses.ts";
import type { components } from "@/client/schema.d.ts";
afterEach(cleanup);
const target = {
  physical_page: 5,
  printed_label: "4",
  anchor: "第一章",
  start: 2,
  sequence: "body",
  markdown_sha256: "a".repeat(64),
  image_sha256: "b".repeat(64),
};
const entry: components["schemas"]["NavigationEntry"] = {
  id: "entry",
  title: "第一章",
  level: 1,
  path: ["第一章"],
  source_page: 1,
  source_anchor: "第一章 3",
  source_image: "/toc.png",
  target_label: "3",
  status: "conflict",
  needs_confirmation: true,
  reason: "目录与正文页码不一致",
  candidates: [target],
};
const report: components["schemas"]["NavigationReport"] = {
  pending_count: 1,
  state: "available",
  version: 1,
  sha256: "c".repeat(64),
  coverage_complete: false,
  entries: [entry],
  approved_pages: [
    { page: 5, headings: ["第一章"], printed_label: "4", image: "/p5.png" },
    {
      page: 8,
      headings: ["第一章 修订稿"],
      printed_label: "3",
      image: "/p8.png",
    },
    { page: 9, headings: [], image: "/p9.png" },
  ],
};
function mount(value = entry) {
  respondWith((request) => {
    const page = Number(new URL(request.url).pathname.split("/").at(-1));
    return {
      page,
      page_count: 9,
      text:
        page === 8
          ? "# 第一章 修订稿\n一九三五年一月\n新的正文开头。"
          : page === 9
            ? "无标题的独立正文开始。\n第二段内容。"
            : "# 第一章\n一九三四年\n旧的正文开头。",
      image: `/p${page}.png`,
      footnotes: [],
    };
  });
  const decide = vi.fn();
  render(
    <QueryClientProvider client={new QueryClient()}>
      <NavigationReview
        entry={value}
        report={report}
        runId="run"
        busy={false}
        onDecide={decide}
        onCancel={() => {}}
      />
    </QueryClientProvider>,
  );
  return decide;
}
test("a rejected candidate leads to title search and requires comparison before saving a different target", async () => {
  const decide = mount();
  await screen.findByText(/旧的正文开头/);
  await userEvent.click(
    screen.getByRole("button", { name: "不是这篇，查找其他正文" }),
  );
  const search = screen.getByRole("searchbox", {
    name: "查找正文标题",
  }) as HTMLInputElement;
  expect(search.value).toBe("第一章");
  await userEvent.clear(search);
  await userEvent.type(search, "修订稿");
  await screen.findByText(/新的正文开头/);
  await userEvent.click(
    screen.getByRole("button", { name: "对照这篇正文 · PDF 第 8 页" }),
  );
  expect(decide).not.toHaveBeenCalled();
  await userEvent.click(
    screen.getByRole("button", { name: /确认跳转到 PDF 第 \d+ 页/ }),
  );
  expect(decide).toHaveBeenCalledWith(
    { page: 8, anchor: "第一章 修订稿" },
    report.sha256,
  );
});
test("a page without headings can use a unique source paragraph as its navigation start", async () => {
  const decide = mount();
  await screen.findByText(/旧的正文开头/);
  await userEvent.click(
    screen.getByRole("button", { name: "不是这篇，查找其他正文" }),
  );
  await userEvent.click(screen.getByText("按 PDF 页序定位"));
  await userEvent.type(
    screen.getByRole("spinbutton", { name: "PDF 页序" }),
    "9",
  );
  await userEvent.click(screen.getByRole("button", { name: "查看这一页" }));
  await screen.findByText("此页没有识别到标题，请从原文中选择正文起点。");
  await userEvent.click(
    screen.getByRole("button", {
      name: "以此处为起点：无标题的独立正文开始。",
    }),
  );
  expect(decide).not.toHaveBeenCalled();
  await screen.findByText(/第二段内容/);
  await userEvent.click(
    screen.getByRole("button", { name: /确认跳转到 PDF 第 \d+ 页/ }),
  );
  expect(decide).toHaveBeenCalledWith(
    { page: 9, anchor: "无标题的独立正文开始。" },
    report.sha256,
  );
});
test("not setting a jump is an explicit final decision and does not claim the article is missing", async () => {
  const decide = mount();
  await screen.findByText(/旧的正文开头/);
  expect(
    screen.getByRole("button", { name: "保留目录文字，不设置跳转" }),
  ).toBeDefined();
  await userEvent.click(
    screen.getByRole("button", { name: "不是这篇，查找其他正文" }),
  );
  expect(
    screen.getByText("预览不会保存。确认后仅更新目录跳转，正文与原件保留。"),
  ).toBeDefined();
  await userEvent.click(
    screen.getByRole("button", { name: "保留目录文字，不设置跳转" }),
  );
  expect(decide).toHaveBeenCalledWith(null, report.sha256);
});

test("Alt+Enter confirms only the displayed and loaded candidate", async () => {
  const decide = mount();
  await screen.findByText(/旧的正文开头/);
  await userEvent.keyboard("{Alt>}{Enter}{/Alt}");
  expect(decide).toHaveBeenCalledExactlyOnceWith(
    { page: 5, anchor: "第一章" },
    report.sha256,
  );
});

test("changed source evidence preserves the preview but prevents a stale confirmation", async () => {
  respondWith(() => ({
    page: 5,
    page_count: 9,
    text: "# 第一章\n旧的正文开头。",
    image: "/p5.png",
    footnotes: [],
  }));
  const decide = vi.fn(),
    reload = vi.fn(),
    query = new QueryClient();
  const view = (value: typeof report) => (
    <QueryClientProvider client={query}>
      <NavigationReview
        entry={value.entries[0]}
        report={value}
        runId="run"
        busy={false}
        onDecide={decide}
        onCancel={() => {}}
        onReload={reload}
      />
    </QueryClientProvider>
  );
  const mounted = render(view(report));
  await screen.findByText(/旧的正文开头/);
  mounted.rerender(
    view({
      ...report,
      version: 2,
      sha256: "d".repeat(64),
      entries: [
        {
          ...entry,
          candidates: [{ ...target, markdown_sha256: "e".repeat(64) }],
        },
      ],
    }),
  );
  expect(
    screen
      .getByRole("button", { name: "确认跳转到 PDF 第 5 页" })
      .hasAttribute("disabled"),
  ).toBe(true);
  await userEvent.keyboard("{Alt>}{Enter}{/Alt}");
  expect(decide).not.toHaveBeenCalled();
  await userEvent.click(
    screen.getByRole("button", { name: "重新载入核对证据" }),
  );
  expect(reload).toHaveBeenCalledOnce();
});

test("ranked candidates can be compared and selected without entering a page number", async () => {
  const decide = mount({
    ...entry,
    candidates: [
      target,
      {
        ...target,
        physical_page: 8,
        printed_label: "3",
        anchor: "第一章 修订稿",
      },
    ],
  });
  await screen.findByRole("button", { name: "查看候选 2 · PDF 第 8 页" });
  await screen.findByText(/新的正文开头/);
  expect(decide).not.toHaveBeenCalled();
  await userEvent.keyboard("{Alt>}{ArrowRight}{/Alt}");
  const confirm = await screen.findByRole("button", {
    name: "确认跳转到 PDF 第 8 页",
  });
  await userEvent.click(confirm);
  expect(decide).toHaveBeenCalledExactlyOnceWith(
    { page: 8, anchor: "第一章 修订稿" },
    report.sha256,
  );
});

test("unrelated directory updates adopt the new version without discarding unchanged visible evidence", async () => {
  respondWith(() => ({
    page: 5,
    page_count: 9,
    text: "# 第一章\n正文。",
    image: "/p5.png",
    footnotes: [],
  }));
  const value = {
    ...entry,
    source_sha256: "1".repeat(64),
    source_image_sha256: "2".repeat(64),
  };
  const first = { ...report, entries: [value] };
  const latest = { ...first, version: 2, sha256: "d".repeat(64) };
  const decide = vi.fn(),
    query = new QueryClient();
  const view = (data: typeof first) => (
    <QueryClientProvider client={query}>
      <NavigationReview
        entry={data.entries[0]}
        report={data}
        runId="run"
        busy={false}
        onDecide={decide}
        onCancel={() => {}}
      />
    </QueryClientProvider>
  );
  const mounted = render(view(first));
  await screen.findByText("正文。");
  mounted.rerender(view(latest));
  await waitFor(() =>
    expect(
      screen
        .getByRole("button", { name: "确认跳转到 PDF 第 5 页" })
        .hasAttribute("disabled"),
    ).toBe(false),
  );
  await userEvent.keyboard("{Alt>}{Enter}{/Alt}");
  expect(decide).toHaveBeenCalledWith(
    { page: 5, anchor: "第一章" },
    latest.sha256,
  );
});

test("reloading changed evidence keeps the chosen candidate and refreshes its confirmation version", async () => {
  respondWith((request) => ({
    page: Number(request.url.split("/").at(-1)),
    page_count: 9,
    text: "# 第一章 修订稿\n修订稿正文。\n# 第一章\n旧的正文。",
    image: "/body.png",
    footnotes: [],
  }));
  const value = {
    ...entry,
    candidates: [
      target,
      { ...target, physical_page: 8, anchor: "第一章 修订稿" },
    ],
  };
  const first = { ...report, entries: [value] };
  const latest = { ...first, version: 2, sha256: "d".repeat(64) };
  const decide = vi.fn(),
    reload = vi.fn(async () => latest),
    query = new QueryClient();
  const view = (data: typeof first) => (
    <QueryClientProvider client={query}>
      <NavigationReview
        entry={data.entries[0]}
        report={data}
        runId="run"
        busy={false}
        onDecide={decide}
        onCancel={() => {}}
        onReload={reload}
      />
    </QueryClientProvider>
  );
  const mounted = render(view(first));
  await userEvent.click(
    await screen.findByRole("button", { name: "查看候选 2 · PDF 第 8 页" }),
  );
  await screen.findByRole("button", { name: "确认跳转到 PDF 第 8 页" });
  mounted.rerender(view(latest));
  await userEvent.click(
    screen.getByRole("button", { name: "重新载入核对证据" }),
  );
  await waitFor(() =>
    expect(
      screen
        .getByRole("button", { name: "确认跳转到 PDF 第 8 页" })
        .hasAttribute("disabled"),
    ).toBe(false),
  );
  await userEvent.keyboard("{Alt>}{Enter}{/Alt}");
  expect(decide).toHaveBeenCalledWith(
    { page: 8, anchor: "第一章 修订稿" },
    latest.sha256,
  );
});

test("a machine recommendation explains its evidence without presenting a score as approval", async () => {
  mount({
    ...entry,
    candidates: [
      {
        ...target,
        match_score: 0.93,
        match_signals: ["标题版式一致", "正文日期一致"],
      },
    ],
  });
  await screen.findByText("标题版式一致");
  expect(screen.getByText("正文日期一致")).toBeDefined();
  expect(screen.queryByText("93%")).toBeNull();
});

test("navigation shortcuts never submit or switch candidates while an input has focus", async () => {
  const decide = mount({
    ...entry,
    candidates: [
      target,
      {
        ...target,
        physical_page: 8,
        anchor: "第一章 修订稿",
      },
    ],
  });
  await screen.findByRole("button", { name: "查看候选 2 · PDF 第 8 页" });
  const zoom = screen.getByRole("slider", {
    name: "目录原图 · PDF 第 1 页放大倍数",
  });
  zoom.focus();
  await userEvent.keyboard("{Alt>}{ArrowRight}{Enter}{/Alt}");
  expect(
    screen.getByRole("button", { name: "确认跳转到 PDF 第 5 页" }),
  ).toBeDefined();
  expect(decide).not.toHaveBeenCalled();
});
