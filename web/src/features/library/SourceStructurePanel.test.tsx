// @vitest-environment happy-dom
import { afterEach, expect, test, vi } from "vitest";
import { act, cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { respondWith } from "@/test/responses";
import { client } from "@/client/api";
import SourceStructurePanel from "./SourceStructurePanel";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});
const snapshot = {
  document_id: "document",
  snapshot_sha256: "a".repeat(64),
  structure_version: 1,
  page_count: 2,
  reviewed_pages: 1,
  page: 1,
  status: "reviewed",
  image: "/page.png",
  image_sha256: "b".repeat(64),
  markdown_sha256: "c".repeat(64),
  coverage: [
    { role: "header", status: "present" },
    { role: "stamp", status: "unknown" },
  ],
  fragments: [
    {
      fragment_id: "p1:h",
      role: "header",
      layer: "source",
      start: 0,
      end: 4,
      text_sha256: "d".repeat(64),
      text: "重印标题",
      text_truncated: false,
    },
  ],
  relations: [],
};
function show() {
  const cache = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={cache}>
      <SourceStructurePanel runId="run" />
    </QueryClientProvider>,
  );
  return cache;
}

test("shows reviewed facts and keeps unknown roles as retained original content", async () => {
  respondWith(() => snapshot);
  show();
  expect(await screen.findByText("重印标题")).toBeTruthy();
  expect(screen.getByText("本页结构已核对")).toBeTruthy();
  expect(screen.getByText("原文字符 0–4")).toBeTruthy();
  expect(
    screen.getByRole("link", { name: "对照本页原图" }).getAttribute("href"),
  ).toBe("/page.png");
  await userEvent.click(screen.getByText("查看全部识别覆盖情况"));
  expect(screen.getByText("印章 · 按原文保留")).toBeTruthy();
  expect(screen.queryByRole("button", { name: /不确定/ })).toBeNull();
});

test("directed recheck submits only the chosen page with its current snapshot and shows a conflict", async () => {
  let body: unknown;
  const middleware = {
    onRequest: async ({ request }: { request: Request }) => {
      if (request.method === "GET") return Response.json(snapshot);
      body = await request.json();
      return Response.json({ detail: "changed" }, { status: 409 });
    },
  };
  client.use(middleware);
  try {
    show();
    await screen.findByText("本页结构已核对");
    await userEvent.click(screen.getByText("补充或重新核对结构"));
    await userEvent.click(
      screen.getByRole("button", { name: "重新核对本页结构" }),
    );
    await screen.findByRole("alert");
    expect(body).toEqual({
      expected_snapshot_sha256: "a".repeat(64),
      force: true,
      pages: [1],
    });
    expect(screen.getByRole("alert").textContent).toContain(
      "结构信息已更新或任务已暂停",
    );
    expect(screen.getByText("重印标题")).toBeTruthy();
  } finally {
    client.eject(middleware);
  }
});

test("another page changing the snapshot does not pretend this page's queued recheck finished", async () => {
  let phase = 0;
  respondWith((request) => {
    if (request.method === "POST") {
      phase = 1;
      return {
        document_id: "document",
        queued_pages: [1],
        skipped: [],
        content_approval: false,
        human_approval: false,
        schema_version: 1,
      };
    }
    return {
      ...snapshot,
      snapshot_sha256: phase ? "b".repeat(64) : snapshot.snapshot_sha256,
      pending_structure_pages: phase === 1 ? 1 : 0,
      structure_task_state: phase === 1 ? "ready" : "completed",
      processing_paused: false,
    };
  });
  const cache = show();
  await screen.findByText("本页结构已核对");
  await userEvent.click(screen.getByText("补充或重新核对结构"));
  await userEvent.click(
    screen.getByRole("button", { name: "重新核对本页结构" }),
  );
  await screen.findByText("还有 1 页结构待核对，完成后自动更新。");
  expect(
    screen.getByRole("button", { name: "重新核对本页结构" }),
  ).toHaveProperty("disabled", true);
  phase = 2;
  await act(async () => {
    await cache.invalidateQueries({
      queryKey: ["platform", "source-structure", "run"],
    });
  });
  await vi.waitFor(() =>
    expect(
      screen.queryByText("还有 1 页结构待核对，完成后自动更新。"),
    ).toBeNull(),
  );
  expect(
    screen.getByRole("button", { name: "重新核对本页结构" }),
  ).toHaveProperty("disabled", false);
});
