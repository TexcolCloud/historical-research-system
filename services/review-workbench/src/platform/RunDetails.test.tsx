// @vitest-environment happy-dom
import { test, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import RunDetails from "./RunDetails";
import { respondWith } from "./test-responses";

test("retry receipt replaces the failed run immediately while the SSE connection is absent", async () => {
  const run = {
    id: "run",
    book_id: "book",
    kind: "book",
    state: "failed",
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
  await screen.findByRole("heading", { name: "处理需要恢复" });
  await userEvent.click(screen.getByRole("button", { name: "重试本阶段" }));
  await screen.findByRole("heading", { name: "书籍正在处理中" });
  expect(screen.queryByRole("heading", { name: "处理需要恢复" })).toBeNull();
});
