// @vitest-environment happy-dom
import { afterEach, expect, test, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import StructurePanel from "@/features/library/StructurePanel.tsx";
import { respondWith } from "@/test/responses.ts";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

test("loads structure only when opened and locates the selected original page", async () => {
  const locate = vi.fn();
  const request = vi.fn(() => ({
    policy: "test",
    available: true,
    items: [
      {
        id: "table",
        kind: "table",
        title: "跨页表格",
        pages: [8, 9],
        status: "ready",
        reason: "行列及数值一致。",
      },
      {
        id: "heading",
        kind: "heading",
        title: "第一节",
        pages: [8],
        status: "retained",
        reason: "等待内容核验。",
        level: 2,
        before: "# 第一节",
        after: "## 第一节",
      },
    ],
  }));
  respondWith(request);
  render(
    <QueryClientProvider client={new QueryClient()}>
      <StructurePanel runId="run" onLocate={locate} />
    </QueryClientProvider>,
  );
  expect(request).not.toHaveBeenCalled();
  await userEvent.click(screen.getByText("结构整理结果"));
  await screen.findByText("跨页表格");
  expect(screen.getByText("等待内容核验。")).toBeTruthy();
  await userEvent.click(
    screen.getAllByRole("button", { name: "查看原件第 9 页" })[0],
  );
  expect(locate).toHaveBeenCalledWith(9);
});
