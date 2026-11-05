// @vitest-environment happy-dom
import { test, expect, afterEach, vi } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import RunDetails from "@/features/runs/RunDetails.tsx";
import ExecutionPanel from "@/features/execution/ExecutionPanel.tsx";
vi.mock("@/components/execution/ExecutionCanvas.tsx", () => ({
  ExecutionCanvas: () => null,
}));
import { respondWith } from "@/test/responses.ts";
import { client } from "@/client/api.ts";

afterEach(cleanup);

test("paused native run offers explicit resume and retains partial counts", async () => {
  respondWith((request) =>
    request.url.includes("/structure")
      ? { available: false, items: [], policy: "test" }
      : [
          {
            id: "run",
            state: "paused",
            execution: { paused: true },
            page_progress: {
              total: 269,
              extracted: 19,
              reviewed: 13,
              approved: 11,
              pending: 2,
            },
          },
        ],
  );
  render(
    <QueryClientProvider client={new QueryClient()}>
      <RunDetails
        book={{
          id: "book",
          run_id: "run",
          title: "书",
          state: "paused",
          stage: "paused",
          revision: 1,
          created_at: "2026-11-04",
          revision_pending: false,
          pending_count: 0,
          navigation_pending_count: 0,
        }}
      />
    </QueryClientProvider>,
  );
  await screen.findByRole("button", { name: "继续处理" });
  expect(screen.getByRole("heading", { name: "自动处理已暂停" })).toBeDefined();
  expect(screen.getByText("已识别 19 / 269 页")).toBeDefined();
});

test("native per-page review does not mark unfinished OCR complete", async () => {
  const run = {
    id: "run",
    book_id: "book",
    kind: "book",
    state: "running",
    stage: "vision",
    revision: 1,
    updated_at: "2026-11-04T00:00:00Z",
    pending_count: 0,
    codex_document_id: "native-document",
    page_progress: {
      total: 269,
      extracted: 19,
      reviewed: 6,
      approved: 6,
      pending: 0,
    },
  };
  respondWith((request) =>
    request.url.includes("/structure")
      ? { available: false, items: [], policy: "test" }
      : [run],
  );
  const query = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={query}>
      <RunDetails
        book={{
          id: "book",
          run_id: "run",
          title: "真实测试材料",
          state: "running",
          stage: "vision",
          created_at: "2026-11-04T00:00:00Z",
          revision: 1,
          revision_pending: false,
          pending_count: 0,
          navigation_pending_count: 0,
        }}
      />
    </QueryClientProvider>,
  );
  await screen.findByText(/Codex 自动处理/);
  expect(
    screen
      .getByRole("heading", { name: /文字提取/ })
      .closest("li")
      ?.getAttribute("data-status"),
  ).toBe("current");
  expect(
    screen
      .getByRole("heading", { name: /原图核对/ })
      .closest("li")
      ?.getAttribute("data-status"),
  ).toBe("current");
  expect(screen.getByText("已识别 19 / 269 页")).toBeDefined();
  expect(screen.getByText("已核验 6 / 269 页")).toBeDefined();
});

test.each(["model_transport_wait", "retrieval_wait", "vision_service_wait"])(
  "execution view distinguishes %s from manual retry",
  async (code) => {
    const run = {
      id: "book-run",
      book_id: "book",
      kind: "book",
      state: "processing",
      stage: "planning",
      revision: 1,
      updated_at: "2026-09-15T00:00:00Z",
      pending_count: 0,
      error: { code, recovery: { retry_at: 1790000000 } },
    };
    respondWith((request) => (request.url.includes("executions") ? [] : [run]));
    const query = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <QueryClientProvider client={query}>
        <ExecutionPanel bookId="book" />
      </QueryClientProvider>,
    );
    expect((await screen.findByRole("status")).textContent).toContain(
      code === "retrieval_wait"
        ? "等待检索服务恢复"
        : code === "vision_service_wait"
          ? "等待本地视觉服务恢复"
          : "等待文本服务恢复",
    );
    expect(screen.getByRole("status").textContent).toContain("已完成结果保留");
    expect(screen.queryByRole("button", { name: "重试本阶段" })).toBeNull();
    query.setQueryData(
      ["platform", "runs", "book"],
      [
        {
          ...run,
          state: "failed",
          error: {
            code: "model_transport_exhausted",
            message: "文本服务恢复等待已达上限，进度保留，请稍后手动重试。",
          },
        },
      ],
    );
    await screen.findByRole("button", { name: "重试本阶段" });
    expect(screen.queryByRole("status")).toBeNull();
    expect(screen.getByRole("alert").textContent).toContain("等待已达上限");
  },
);

test.each(["book"])(
  "retry receipt replaces a failed/revision %s run while SSE is absent",
  async (kind) => {
    const run = {
      id: "run",
      book_id: "book",
      kind,
      state: kind === "cards" ? "needs_revision" : "failed",
      stage: "vision",
      revision: 1,
      updated_at: "2026-09-13T00:00:00Z",
      pending_count: 0,
    };
    respondWith((request) =>
      request.method === "POST"
        ? { ...run, state: "queued", revision: 2 }
        : [run],
    );
    const query = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <QueryClientProvider client={query}>
        <RunDetails
          book={{
            id: "book",
            run_id: "run",
            title: "技术材料",
            state: "failed",
            stage: "vision",
            created_at: "2026-09-13T00:00:00Z",
            revision: 1,
            revision_pending: false,
            pending_count: 0,
            navigation_pending_count: 0,
          }}
        />
      </QueryClientProvider>,
    );
    const heading =
      kind === "cards" ? "部分卡片未通过机器核验" : "处理需要恢复";
    await screen.findByRole("heading", { name: heading });
    await userEvent.click(screen.getByRole("button", { name: "重试本阶段" }));
    await screen.findByRole("heading", { name: "已排队，等待处理" });
    expect(screen.queryByRole("heading", { name: heading })).toBeNull();
  },
);

test("historical card execution remains readable without restarting generation", async () => {
  const run = {
    id: "card-run",
    book_id: "book",
    kind: "cards",
    state: "needs_revision",
    stage: "machine_review",
    revision: 1,
    updated_at: "2026-09-14T00:00:00Z",
    pending_count: 0,
  };
  respondWith((request) =>
    request.method === "POST"
      ? { ...run, state: "queued", revision: 2 }
      : request.url.includes("executions")
        ? []
        : [run],
  );
  const query = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={query}>
      <ExecutionPanel bookId="book" />
    </QueryClientProvider>,
  );
  await screen.findByRole("option", { name: /历史史料卡任务/ });
  expect(screen.queryByRole("button", { name: "重试本阶段" })).toBeNull();
});

test("execution overview keeps true totals and requests history only on demand", async () => {
  const requests: string[] = [];
  respondWith((request) => {
    requests.push(request.url);
    return request.url.includes("execution-window")
      ? {
          total: 1012,
          matching: request.url.includes("history=true") ? 1012 : 1,
          offset: 0,
          limit: 40,
          stages: [
            { label: "原页核验", state: "completed", count: 1011 },
            { label: "原页核验", state: "failed", count: 1 },
          ],
          records: [],
        }
      : [{ id: "run", kind: "book", state: "running", revision: 1 }];
  });
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <ExecutionPanel bookId="book" />
    </QueryClientProvider>,
  );
  await screen.findByText(/累计 1012 条处理记录/);
  expect(requests.some((url) => url.includes("history=true"))).toBe(false);
  await userEvent
    .setup()
    .click(screen.getByRole("button", { name: "查看历史记录" }));
  await screen.findByRole("button", { name: "查看当前与异常" });
  expect(requests.some((url) => url.includes("history=true"))).toBe(true);
});

test("switching execution windows preserves totals while the next batch loads", async () => {
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  const middleware = {
    onRequest: async ({ request }: { request: Request }) => {
      if (request.url.includes("history=true")) await gate;
      return Response.json(
        request.url.includes("execution-window")
          ? {
              total: 1012,
              matching: 1012,
              records: [],
              stages: [],
              offset: 0,
              limit: 40,
            }
          : [{ id: "run", kind: "book", state: "running", revision: 1 }],
      );
    },
  };
  client.use(middleware);
  const query = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  try {
    render(
      <QueryClientProvider client={query}>
        <ExecutionPanel bookId="book" />
      </QueryClientProvider>,
    );
    await screen.findByText(/累计 1012 条处理记录/);
    await userEvent
      .setup()
      .click(screen.getByRole("button", { name: "查看历史记录" }));
    expect(screen.queryByText(/累计 1012 条处理记录/)).not.toBeNull();
    expect(screen.getByRole("status").textContent).toContain(
      "正在读取所选记录",
    );
  } finally {
    release();
    client.eject(middleware);
    query.clear();
  }
});

test.each([false, true])(
  "capacity notice respects completed book state (%s)",
  async (completed) => {
    respondWith((request) =>
      request.url.includes("/structure")
        ? { available: false, items: [], policy: "test" }
        : [
            {
              id: "run",
              state: completed ? "completed" : "running",
              stage: "vision",
              execution: {
                coordinator_active: true,
                automatic_work_remaining: !completed,
              },
              scheduling: {
                capacity: 14,
                batch_size: 8,
                active: {},
                workers: {},
                metrics: {},
                cooldown_until: 0,
                runtime_control: {
                  state: "cooldown",
                  scopes: [
                    {
                      scope: "model",
                      model: "gpt-6.1-terra",
                      state: "cooldown",
                      code: "capacity",
                      limit: 3,
                      configured_limit: 7,
                      retry_at: 1794000000,
                    },
                  ],
                },
              },
            },
          ],
    );
    render(
      <QueryClientProvider client={new QueryClient()}>
        <RunDetails
          book={{
            id: "book",
            run_id: "run",
            title: "史料",
            state: completed ? "ready" : "running",
            stage: "vision",
            revision: 1,
            created_at: "2026-11-06",
            revision_pending: false,
            pending_count: 0,
            navigation_pending_count: 0,
          }}
        />
      </QueryClientProvider>,
    );
    await screen.findByRole("heading", {
      name: completed ? "本书处理完成" : "等待服务恢复",
    });
    const notice = screen.queryByRole("status", { name: "自动恢复状态" });
    expect(Boolean(notice)).toBe(!completed);
    if (!completed) {
      expect(notice?.textContent).toContain("Terra：服务暂时容量不足");
      expect(screen.queryByText("本书正在自动处理。")).toBeNull();
    }
  },
);
