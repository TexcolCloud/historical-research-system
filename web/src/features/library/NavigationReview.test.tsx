// @vitest-environment happy-dom
import { afterEach, expect, test, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
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
function mount() {
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
        entry={entry}
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
