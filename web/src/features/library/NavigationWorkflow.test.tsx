// @vitest-environment happy-dom
import { afterEach, expect, test, vi } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import NavigationPanel from "./NavigationPanel.tsx";
import { RuntimeScopeContext } from "@/app/runtime-scope.ts";
import { respondWith } from "@/test/responses.ts";
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

test("directory editing stays inside the chosen entry and waiting evidence is not a human action", async () => {
  const entry = {
    id: "conflict",
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
    target: null,
    candidates: [
      {
        physical_page: 5,
        printed_label: "4",
        anchor: "第一章",
        start: 0,
        markdown_sha256: "a".repeat(64),
        image_sha256: "b".repeat(64),
      },
    ],
  };
  respondWith((request) =>
    request.url.includes("review-pages")
      ? {
          page: 5,
          page_count: 5,
          text: "# 第一章\n本页正文。",
          image: "/page5.png",
          footnotes: [],
        }
      : {
          version: 1,
          sha256: "c".repeat(64),
          coverage_complete: false,
          state: "available",
          approved_pages: [
            {
              page: 5,
              printed_label: "4",
              headings: ["第一章"],
              image: "/page5.png",
            },
          ],
          entries: [
            entry,
            {
              ...entry,
              id: "waiting",
              title: "等待中的第二章",
              status: "waiting_evidence",
              needs_confirmation: false,
              candidates: [],
            },
          ],
        },
  );
  render(
    <MemoryRouter>
      <QueryClientProvider client={new QueryClient()}>
        <RuntimeScopeContext.Provider value="content">
          <NavigationPanel runId="run" bookId="book" />
        </RuntimeScopeContext.Provider>
      </QueryClientProvider>
    </MemoryRouter>,
  );
  const adjust = await screen.findAllByRole("button", { name: "确认目录跳转" });
  expect(adjust).toHaveLength(1);
  await userEvent.click(adjust[0]);
  const editor = screen.getByRole("region", { name: "目录跳转核对" });
  expect(editor.closest("li")?.textContent).toContain("第一章");
  expect(screen.queryByRole("button", { name: "暂不关联" })).toBeNull();
  expect(
    within(editor).getByAltText("目录原图 · PDF 第 1 页").getAttribute("src"),
  ).toBe("/toc.png");
  await screen.findByText("本页正文。");
  const confirm = within(editor).getByRole("button", {
    name: "就是这篇，确认跳转",
  });
  expect(confirm.hasAttribute("disabled")).toBe(false);
  expect(
    within(editor).getByAltText("正文原图 · PDF 第 5 页").getAttribute("src"),
  ).toBe("/page5.png");
});
