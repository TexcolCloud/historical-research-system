// @vitest-environment happy-dom
import { afterEach, expect, test, vi } from "vitest";
import { act, cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import ResearchRunPanel from "./ResearchRun";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

test.each([
  ["ready", "史料卡待制作"],
  ["pending", "史料卡待人工核对"],
  ["failed", "史料卡遇到技术故障"],
  ["completed", "史料卡已完成"],
])(
  "compact progress retains actual %s status and mounts management only on demand",
  async (state, label) => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        Response.json({
          run_id: "compact",
          book_id: "book",
          state,
          version: 1,
          card_only: true,
          counts: {
            articles_total: 4,
            articles_completed: state === "completed" ? 4 : 1,
            articles_pending: state === "pending" ? 1 : 0,
          },
          card_ids: [],
          tasks: [],
        }),
      ),
    );
    render(
      <QueryClientProvider
        client={
          new QueryClient({ defaultOptions: { queries: { retry: false } } })
        }
      >
        <ResearchRunPanel runId="compact" compact />
      </QueryClientProvider>,
    );
    await screen.findByText(label);
    expect(screen.queryByRole("progressbar")).toBeNull();
    expect(screen.queryByText("制卡流程")).toBeNull();
    await userEvent.click(screen.getByText("查看处理详情"));
    expect(await screen.findByRole("progressbar")).toBeTruthy();
    if (state !== "completed")
      expect(screen.queryByText("史料卡已完成")).toBeNull();
  },
);

test("automatic cards expose leased stages and separate machine progress from human approval", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      Response.json({
        run_id: "run",
        book_id: "book",
        state: "running",
        version: 3,
        card_only: true,
        counts: {
          articles_total: 2,
          articles_completed: 0,
          articles_pending: 1,
        },
        card_ids: ["pending-card"],
        tasks: [
          { task_id: "author", stage: "author", state: "completed" },
          { task_id: "verify", stage: "verify", state: "completed" },
          { task_id: "repair", stage: "repair", state: "completed" },
          { task_id: "reverify", stage: "reverify", state: "leased" },
        ],
      }),
    ),
  );
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <ResearchRunPanel runId="run" />
    </QueryClientProvider>,
  );
  await screen.findByText("史料卡正在修正复核");
  expect(screen.getByText(/修正复核.*处理中/)).toBeTruthy();
  expect(
    screen
      .getByRole("progressbar", { name: "制卡机器处理进度" })
      .getAttribute("value"),
  ).toBe("1");
  expect(screen.getByText(/已办结 0.*待人工核对 1/)).toBeTruthy();
  expect(screen.queryByText("史料卡已完成")).toBeNull();
});

test("automatic repair and re-verification remain machine work and expose their actual stage names", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      Response.json({
        run_id: "run",
        book_id: "book",
        state: "running",
        version: 2,
        counts: {
          articles_total: 1,
          articles_completed: 0,
          articles_pending: 0,
        },
        card_ids: [],
        tasks: [
          { task_id: "verify", stage: "verify", state: "completed" },
          { task_id: "repair", stage: "repair", state: "running" },
          { task_id: "reverify", stage: "reverify", state: "ready" },
        ],
      }),
    ),
  );
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <ResearchRunPanel runId="run" />
    </QueryClientProvider>,
  );
  expect(await screen.findByText(/定向修订.*处理中/)).toBeTruthy();
  expect(await screen.findByText(/再次独立核验.*等待处理/)).toBeTruthy();
  expect(
    screen.queryByText("请打开待人工核验的卡片，对照原文作出决定。"),
  ).toBeNull();
});

test("queue and running progress poll only the lightweight task until human review is needed", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  const requests: string[] = [];
  const states = ["ready", "running", "pending"];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      requests.push(String(input));
      return Response.json({
        run_id: "run",
        book_id: "book",
        version: requests.length,
        state: states[Math.min(requests.length - 1, 2)],
        counts: {
          articles_total: 1,
          articles_completed: 0,
          articles_pending: 0,
        },
        card_ids: [],
      });
    }),
  );
  const query = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={query}>
      <ResearchRunPanel runId="run" />
    </QueryClientProvider>,
  );
  await screen.findByText("研究任务等待处理");
  await act(async () => {
    await vi.advanceTimersByTimeAsync(10001);
  });
  expect(requests).toHaveLength(2);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(5001);
  });
  await screen.findByText("研究任务待人工核验");
  await act(async () => {
    await vi.advanceTimersByTimeAsync(60000);
  });
  expect(requests).toEqual([
    "/api/v2/research/runs/run",
    "/api/v2/research/runs/run",
    "/api/v2/research/runs/run",
  ]);
});

test("a late running response cannot undo a successful pause", async () => {
  let resolveLate!: (response: Response) => void;
  let reads = 0;
  const running = {
    run_id: "run",
    book_id: "book",
    version: 1,
    state: "running",
    counts: { articles: 3, completed: 1, pending: 0 },
    tasks: [],
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      if (String(input).endsWith("/pause"))
        return Response.json({ ...running, state: "paused", version: 3 });
      if (++reads === 1) return Response.json(running);
      return new Promise<Response>((resolve) => {
        resolveLate = resolve;
      });
    }),
  );
  const query = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={query}>
      <ResearchRunPanel runId="run" />
    </QueryClientProvider>,
  );
  await screen.findByRole("button", { name: "暂停旧任务" });
  act(() => {
    void query.refetchQueries({ queryKey: ["research", "run", "run"] });
  });
  await vi.waitFor(() => expect(reads).toBe(2));
  await userEvent.click(screen.getByRole("button", { name: "暂停旧任务" }));
  await screen.findByText("研究任务已暂停");
  await act(async () => {
    resolveLate(Response.json({ ...running, version: 2 }));
  });
  expect(screen.getByText("研究任务已暂停")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "暂停旧任务" })).toBeNull();
  expect(
    screen.getByRole("button", { name: "恢复旧任务（保留原流程）" }),
  ).toBeTruthy();
  expect(query.getQueryData(["research", "run", "run"])).toMatchObject({
    state: "paused",
    version: 3,
  });
});
