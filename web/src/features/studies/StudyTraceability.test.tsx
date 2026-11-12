// @vitest-environment happy-dom
import { afterEach, expect, test, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { ArgumentsPage } from "./StudyRecords";
import { CanvasInspector } from "./canvas-inspector";
import type { StudyRecord, StudyTopic } from "./types";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  localStorage.clear();
});

test("adopted records retain an exact analysis return link in their list, editor, and canvas detail", async () => {
  const topic: StudyTopic = {
    id: "专题 /1",
    title: "专题",
    description: "",
    book_ids: [],
    materials: [],
    version: 1,
    archived: false,
    created_at: 1,
    updated_at: 1,
  };
  const record: StudyRecord = {
    id: "record",
    topic_id: topic.id,
    kind: "argument",
    title: "采纳后编辑的个人解释",
    text: "这是个人解释。",
    limitations: "仍须回看原分析依据。",
    source_refs: [],
    origin: "ai_adopted",
    analysis_id: "analysis/?#中文",
    version: 3,
    archived: false,
    created_at: 1,
    updated_at: 1,
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      return Response.json(
        path.includes("/records?")
          ? { items: [record], total: 1, offset: 0, limit: 20 }
          : path.includes("/topics?")
            ? { items: [topic], total: 1, offset: 0, limit: 100 }
            : topic,
      );
    }),
  );
  const query = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const view = render(
    <MemoryRouter
      initialEntries={[
        `/arguments?${new URLSearchParams({ topic: topic.id })}`,
      ]}
    >
      <QueryClientProvider client={query}>
        <ArgumentsPage />
      </QueryClientProvider>
    </MemoryRouter>,
  );
  function assertLink(link: HTMLElement) {
    const href = new URL(link.getAttribute("href") ?? "", "http://localhost");
    expect(href.pathname).toBe("/analysis");
    expect(href.searchParams.get("topic")).toBe(topic.id);
    expect(href.searchParams.get("analysis")).toBe(record.analysis_id);
  }
  assertLink(await screen.findByRole("link", { name: "查看原分析与采纳依据" }));
  await userEvent.click(screen.getByRole("button", { name: "编辑记录" }));
  const links = screen.getAllByRole("link", { name: "查看原分析与采纳依据" });
  // Editing replaces the selected document; the list no longer duplicates full content.
  expect(links).toHaveLength(1);
  links.forEach(assertLink);
  expect(screen.getByText("个人整理，不改变原文核验状态")).toBeTruthy();
  view.unmount();
  render(
    <MemoryRouter>
      <QueryClientProvider client={query}>
        <CanvasInspector
          topic={topic}
          document={{ nodes: [], edges: [], viewport: { x: 0, y: 0, z: 1 } }}
          node={{
            id: "node",
            record_kind: "record",
            record_id: record.id,
            x: 0,
            y: 0,
            width: 280,
            height: 220,
          }}
          record={record}
          onRetryRecord={vi.fn()}
          readonly={false}
          editing={null}
          onEdit={vi.fn()}
          onSaved={vi.fn()}
          onClose={vi.fn()}
          onRemove={vi.fn()}
          onUpdate={vi.fn()}
          returnTo="/workspace"
        />
      </QueryClientProvider>
    </MemoryRouter>,
  );
  assertLink(screen.getByRole("link", { name: "查看原分析与采纳依据" }));
  expect(screen.getByText("AI 建议采纳 · 个人记录")).toBeTruthy();
});
