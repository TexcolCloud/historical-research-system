// @vitest-environment happy-dom
import { test, expect, afterEach, vi } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import RunDetails from "./RunDetails";
import ExecutionPanel from "./ExecutionPanel";
vi.mock("../components/execution/ExecutionCanvas", () => ({
  ExecutionCanvas: () => null,
}));
import { respondWith } from "./test-responses";

afterEach(cleanup);

test.each(["book", "cards"])(
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
          }}
        />
      </QueryClientProvider>,
    );
    const heading =
      kind === "cards" ? "部分卡片未通过机器核验" : "处理需要恢复";
    await screen.findByRole("heading", { name: heading });
    await userEvent.click(screen.getByRole("button", { name: "重试本阶段" }));
    await screen.findByRole("heading", { name: "书籍正在处理中" });
    expect(screen.queryByRole("heading", { name: heading })).toBeNull();
  },
);

test("execution view offers recovery for a card needing revision", async () => {
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
  await userEvent.click(
    await screen.findByRole("button", { name: "重试本阶段" }),
  );
  expect(query.getQueryData(["platform", "run", run.id])).toMatchObject({
    state: "queued",
  });
});
