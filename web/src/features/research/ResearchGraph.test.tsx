// @vitest-environment happy-dom
import { afterEach, expect, test, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import ResearchGraph from "./ResearchGraph";
vi.mock("@xyflow/react", () => ({
  ReactFlow: () => null,
  Background: () => null,
  Controls: () => null,
}));
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

test("system graph edges have Chinese labels in their action and selected evidence heading", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      Response.json({
        entity_id: "person",
        hops: 1,
        limit: 50,
        truncated: false,
        nodes: [
          { id: "person", kind: "person", name: "陈毅" },
          { id: "document", kind: "card", name: "文献", card_id: "card" },
        ],
        edges: [
          {
            id: "edge",
            source_id: "person",
            target_id: "document",
            kind: "mentioned_in",
            predicate: "mentioned_in",
            reason: "来源记载",
            card_id: "card",
            evidence: [],
          },
        ],
      }),
    ),
  );
  render(
    <MemoryRouter>
      <QueryClientProvider
        client={
          new QueryClient({ defaultOptions: { queries: { retry: false } } })
        }
      >
        <ResearchGraph
          entityId="person"
          onEntity={() => {}}
          onCard={() => {}}
        />
      </QueryClientProvider>
    </MemoryRouter>,
  );
  await userEvent.click(
    await screen.findByRole("button", { name: "查看关系依据 · 见于材料" }),
  );
  expect(screen.getByRole("heading", { name: "见于材料" })).toBeTruthy();
  expect(screen.queryByText("mentioned_in")).toBeNull();
});
test("a relationship view opens the other endpoint and the retained evidence behind an edge", async () => {
  const entity = vi.fn(),
    card = vi.fn();
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      Response.json({
        entity_id: "person",
        hops: 1,
        limit: 50,
        truncated: false,
        nodes: [
          { id: "person", kind: "person", name: "张某" },
          { id: "event", kind: "event", name: "拨款申请事件" },
        ],
        edges: [
          {
            id: "edge",
            source_id: "person",
            target_id: "event",
            predicate: "发函者",
            kind: "participant",
            reason: "仅记载提出申请",
            card_id: "card",
            evidence: [
              {
                id: "e",
                quote: "张某函请拨款。",
                pages: [5],
                footnote_ids: [],
              },
            ],
          },
        ],
      }),
    ),
  );
  render(
    <MemoryRouter>
      <QueryClientProvider
        client={
          new QueryClient({ defaultOptions: { queries: { retry: false } } })
        }
      >
        <ResearchGraph entityId="person" onEntity={entity} onCard={card} />
      </QueryClientProvider>
    </MemoryRouter>,
  );
  await userEvent.click(
    await screen.findByRole("button", { name: "拨款申请事件" }),
  );
  expect(entity).toHaveBeenCalledWith("event");
  await userEvent.click(
    screen.getByRole("button", { name: "查看关系依据 · 发函者" }),
  );
  await screen.findByText("张某函请拨款。");
  await userEvent.click(screen.getByRole("button", { name: "查看出处卡片" }));
  expect(card).toHaveBeenCalledWith("card");
});
