// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import App from "./App";
import { respondWith } from "@/test/responses";
import { research } from "@/features/research/api";
import type { UploadActivity } from "@/features/uploads/UploadPanel";

beforeEach(() => {
  vi.spyOn(research, "pending").mockResolvedValue({
    items: [],
    total: 0,
    offset: 0,
    limit: 50,
  });
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) =>
      Response.json(
        String(input).includes("/preferences")
          ? { version: 0, compact: false, restore_last_canvas: true }
          : { items: [], total: 0, offset: 0, limit: 100 },
      ),
    ),
  );
});

const lifetime = vi.hoisted(() => ({ mounts: 0, stops: 0 }));
vi.mock("@/routes/BookPage.tsx", () => ({
  default: () => <h1>书籍详情</h1>,
}));
vi.mock("@/features/uploads/UploadPanel.tsx", async () => {
  const { useEffect } = await import("react");
  return {
    default: function Upload({
      onActivityChange,
    }: {
      onActivityChange?: (value: UploadActivity) => void;
    }) {
      useEffect(() => {
        lifetime.mounts++;
        return () => {
          lifetime.stops++;
        };
      }, []);
      return (
        <div data-testid="upload-session">
          synthetic upload
          <button
            onClick={() =>
              onActivityChange?.({ total: 1, pending: 1, uploading: 0 })
            }
          >
            合成选择文件
          </button>
          <button
            onClick={() =>
              onActivityChange?.({ total: 1, pending: 1, uploading: 1 })
            }
          >
            合成开始上传
          </button>
          <button
            onClick={() =>
              onActivityChange?.({ total: 1, pending: 0, uploading: 0 })
            }
          >
            合成上传完成
          </button>
        </div>
      );
    },
  };
});

test("the header distinguishes a selected file from a running transfer and a completed upload", async () => {
  vi.stubGlobal(
    "EventSource",
    class {
      close() {}
    },
  );
  respondWith((request) =>
    request.url.includes("/events/snapshot")
      ? { books: [], cursor: 0 }
      : request.url.includes("/runtime")
        ? { validation_scope: "content" }
        : [],
  );
  const query = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const view = render(
    <MemoryRouter>
      <QueryClientProvider client={query}>
        <App />
      </QueryClientProvider>
    </MemoryRouter>,
  );
  const user = userEvent.setup();
  await screen.findByRole("heading", { name: "仪表盘" });
  const navigation = within(
    screen.getByRole("navigation", { name: "主要导航" }),
  );
  expect(navigation.getAllByRole("link")).toHaveLength(10);
  for (const name of [
    "仪表盘",
    "全文检索",
    "史料集",
    "论据丛",
    "研究专题",
    "专题工作台",
    "时间线索",
    "AI 分析",
    "设置",
    "更新日志",
  ])
    expect(navigation.getByRole("link", { name })).toBeTruthy();
  expect(
    navigation
      .getByRole("link", { name: "仪表盘" })
      .getAttribute("aria-current"),
  ).toBe("page");
  await user.click(await screen.findByRole("button", { name: "上传史料" }));
  await user.click(await screen.findByRole("button", { name: "合成选择文件" }));
  expect(
    screen.getByRole("button", { name: "查看导入进展" }).textContent,
  ).toContain("待上传 · 1");
  await user.click(screen.getByRole("button", { name: "合成开始上传" }));
  expect(
    screen.getByRole("button", { name: "查看导入进展" }).textContent,
  ).toContain("上传中 · 1");
  await user.click(screen.getByRole("button", { name: "合成选择文件" }));
  expect(
    screen.getByRole("button", { name: "查看导入进展" }).textContent,
  ).toContain("待上传 · 1");
  await user.click(screen.getByRole("button", { name: "合成上传完成" }));
  expect(screen.getByRole("button", { name: "查看导入进展" }).textContent).toBe(
    "上传史料",
  );
  view.unmount();
  query.clear();
});
afterEach(() => {
  cleanup();
  lifetime.mounts = 0;
  lifetime.stops = 0;
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

test("retired introduction redirects to books while navigation retains one stream and active upload", async () => {
  const close = vi.fn();
  const streams: string[] = [];
  vi.stubGlobal(
    "EventSource",
    class {
      close = close;
      constructor(url: string) {
        streams.push(url);
      }
    },
  );
  respondWith((request) =>
    request.url.includes("/events/snapshot")
      ? { books: [], cursor: 42 }
      : request.url.includes("/runtime")
        ? { validation_scope: "content" }
        : [],
  );
  const query = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const view = render(
    <MemoryRouter initialEntries={["/welcome"]}>
      <QueryClientProvider client={query}>
        <App />
      </QueryClientProvider>
    </MemoryRouter>,
  );
  expect(screen.queryByRole("link", { name: "史料卡" })).toBeNull();
  expect(screen.queryByRole("link", { name: "研究检索" })).toBeNull();
  expect(screen.getByRole("link", { name: "实体库" })).toBeTruthy();
  const user = userEvent.setup();
  await user.click(
    await screen.findByRole("button", { name: "上传史料" }, { timeout: 5000 }),
  );
  await screen.findByTestId("upload-session");
  await user.click(screen.getByRole("link", { name: "待办" }));
  await screen.findByRole("heading", { name: "待办" });
  expect(screen.queryByRole("link", { name: "产品介绍" })).toBeNull();
  await user.click(screen.getByRole("link", { name: "史料集" }));
  await screen.findByRole("heading", { name: "书籍" });
  expect(screen.getByTestId("upload-session")).toBeTruthy();
  expect(lifetime.mounts).toBe(1);
  expect(lifetime.stops).toBe(0);
  expect(streams).toEqual(["/api/v2/events?after=42"]);
  expect(close).not.toHaveBeenCalled();
  view.unmount();
  expect(lifetime.stops).toBe(1);
  expect(close).toHaveBeenCalledTimes(1);
  query.clear();
});

test("returning from book details retains the shelf search and status filter", async () => {
  vi.stubGlobal(
    "EventSource",
    class {
      close() {}
    },
  );
  respondWith((request) =>
    request.url.includes("/events/snapshot")
      ? { books: [], cursor: 0 }
      : request.url.includes("/runtime")
        ? { validation_scope: "content" }
        : [
            {
              id: "review",
              title: "待审史料",
              state: "awaiting_review",
              created_at: "2026-09-01",
            },
            {
              id: "ready",
              title: "已入库史料",
              state: "ready",
              created_at: "2026-09-01",
            },
          ],
  );
  const query = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const view = render(
    <MemoryRouter initialEntries={["/library"]}>
      <QueryClientProvider client={query}>
        <App />
      </QueryClientProvider>
    </MemoryRouter>,
  );
  const user = userEvent.setup();
  await user.type(
    await screen.findByRole("textbox", { name: "搜索书籍" }),
    "史料",
  );
  await user.click(screen.getByRole("tab", { name: "需要核对" }));
  await user.click(await screen.findByRole("button", { name: /^待审史料/ }));
  await screen.findByRole("heading", { name: "书籍详情" });
  expect(
    screen.getByRole("link", { name: "史料集" }).getAttribute("aria-current"),
  ).toBe("page");
  await user.click(screen.getByRole("link", { name: "史料集" }));
  expect(
    (
      (await screen.findByRole("textbox", {
        name: "搜索书籍",
      })) as HTMLInputElement
    ).value,
  ).toBe("史料");
  expect(
    screen.getByRole("tab", { name: "需要核对" }).getAttribute("aria-selected"),
  ).toBe("true");
  expect(await screen.findByRole("button", { name: /^待审史料/ })).toBeTruthy();
  expect(screen.queryByRole("button", { name: /^已入库史料/ })).toBeNull();
  view.unmount();
  query.clear();
});
