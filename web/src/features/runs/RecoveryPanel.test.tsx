// @vitest-environment happy-dom
import { afterEach, expect, test } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import RecoveryPanel from "./RecoveryPanel";
import { respondWith } from "@/test/responses";
afterEach(cleanup);
test("recovery shows preserved work and one primary action, then retains the durable request", async () => {
  const requests: unknown[] = [];
  const run = {
    id: "run",
    book_id: "book",
    state: "failed",
    stage: "vision",
    revision: 1,
    updated_at: "2026-11-05",
    kind: "book",
    pending_count: 0,
    codex_document_id: "native",
  };
  respondWith((request) => {
    if (request.method === "POST") {
      requests.push(request);
      return {
        ...run,
        state: "queued",
        revision: 2,
        recovery: { state: "checking" },
      };
    }
    return {
      state: "available",
      reason: "自动回退未成功，请检查后恢复。",
      preserved_ocr: 155,
      preserved_approved: 152,
      pending_human: 2,
      stage: "verify",
      page: 155,
      automatic_checkpoint_used: true,
    };
  });
  const query = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={query}>
      <RecoveryPanel run={run} />
    </QueryClientProvider>,
  );
  await screen.findByText(/已保留.*155.*152/);
  expect(screen.getAllByRole("button", { name: "从断点恢复" })).toHaveLength(1);
  await userEvent
    .setup()
    .click(screen.getByRole("button", { name: "从断点恢复" }));
  expect(requests).toHaveLength(1);
  await screen.findByRole("button", { name: "恢复请求已保存" });
  expect(screen.getByText(/第 155 页/)).toBeTruthy();
});

test("quota blocking explains the condition beside the single checkpoint action", async () => {
  const runtime = {
    state: "blocked",
    scopes: [
      {
        scope: "account",
        model: null,
        state: "blocked",
        code: "quota_exhausted",
        limit: 0,
        configured_limit: 14,
        retry_at: null,
      },
    ],
  };
  respondWith(() => ({
    state: "blocked",
    reason: "账户额度已用尽，请在额度恢复后从断点恢复。",
    unfinished_tasks: 2,
    preserved_ocr: 2,
    preserved_approved: 1,
    runtime_control: runtime,
  }));
  render(
    <QueryClientProvider client={new QueryClient()}>
      <RecoveryPanel
        run={{
          id: "run",
          book_id: "book",
          state: "failed",
          stage: "vision",
          revision: 1,
          updated_at: "2026-11-06",
          codex_document_id: "native",
          pending_count: 0,
          kind: "book",
        }}
      />
    </QueryClientProvider>,
  );
  const notice = await screen.findByRole("status", { name: "自动恢复状态" });
  expect(notice.textContent).toContain("当前账户：账户额度已用尽");
  expect(screen.getAllByRole("button", { name: "从断点恢复" })).toHaveLength(1);
  expect(screen.queryByText("系统正在尝试自动恢复，暂无需操作。")).toBeNull();
});
