// @vitest-environment happy-dom
import { afterEach, expect, test, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import EntityTimeline from "./EntityTimeline";
import type { ResearchTimelineItem } from "./api";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
test("controlled timeline selects the exact source event without navigating or fetching evidence", async () => {
  const item: ResearchTimelineItem = {
    id: "source-mention",
    entity_id: "canonical-event",
    item_kind: "event",
    name: "拟拨经费",
    description: "材料所载计划",
    card_id: "card",
    card_version: 2,
    book_id: "book",
    run_id: "run",
    date_precision: "year",
    status_note: "",
    source_current: true,
    needs_update: false,
    date: { text: "民国二十年", certainty: "inferred", normalized: "1931" },
    date_basis: "event",
    undated: false,
    event_status: "planned",
    evidence_count: 1,
    evidence_ids: ["e"],
    participants: [{ entity_id: "person", name: "甲某", role: "拟任负责人" }],
    participant_count: 1,
  };
  const select = vi.fn(),
    network = vi.fn(),
    entity = vi.fn(),
    card = vi.fn(),
    request = vi.fn();
  vi.stubGlobal("fetch", request);
  render(
    <QueryClientProvider client={new QueryClient()}>
      <EntityTimeline
        entityId="person"
        onEntity={entity}
        onCard={card}
        data={{
          items: [item],
          total: 1,
          offset: 0,
          limit: 20,
          truncated: false,
          dated_total: 1,
          undated_total: 0,
          date_basis: "event",
        }}
        selectedEventId="source-mention"
        onSelectEvent={select}
        onViewInNetwork={network}
      />
    </QueryClientProvider>,
  );
  const title = screen.getByRole("button", { name: "拟拨经费" });
  expect(title.getAttribute("aria-pressed")).toBe("true");
  await userEvent.click(title);
  expect(select).toHaveBeenCalledWith(item);
  expect(screen.getByText("计划或拟议事项")).toBeTruthy();
  expect(screen.getByText("甲某（拟任负责人）")).toBeTruthy();
  expect(screen.getByText(/日期据材料推定/)).toBeTruthy();
  await userEvent.click(
    screen.getByRole("button", { name: "在关系网中查看 · 拟拨经费" }),
  );
  expect(network).toHaveBeenCalledWith(item);
  expect(entity).not.toHaveBeenCalled();
  expect(card).not.toHaveBeenCalled();
  expect(request).not.toHaveBeenCalled();
  expect(screen.queryByText("核对事项依据")).toBeNull();
});
test("timeline separates undated records and reports the real page range", () => {
  const item: ResearchTimelineItem = {
    id: "dated",
    item_kind: "event",
    entity_id: "event",
    name: "有日期的事项",
    description: "",
    card_id: "card",
    card_version: 1,
    book_id: "book",
    run_id: "run",
    date_precision: "year",
    status_note: "",
    source_current: true,
    needs_update: false,
    date: { text: "1931年", certainty: "stated" },
    date_basis: "event",
    undated: false,
    event_status: "negated",
    evidence_count: 0,
    evidence_ids: [],
  };
  render(
    <QueryClientProvider client={new QueryClient()}>
      <EntityTimeline
        entityId="person"
        onEntity={vi.fn()}
        onCard={vi.fn()}
        onSelectEvent={vi.fn()}
        data={{
          items: [
            item,
            {
              ...item,
              id: "undated",
              name: "未载日期的事项",
              date: { text: "", certainty: "unknown" },
              date_precision: "unknown",
              undated: true,
            },
          ],
          total: 42,
          dated_total: 30,
          undated_total: 12,
          offset: 20,
          limit: 20,
          truncated: true,
          date_basis: "event",
        }}
      />
    </QueryClientProvider>,
  );
  expect(screen.getByRole("heading", { name: "未定年" })).toBeTruthy();
  expect(screen.getByText("显示第 21–22 项，共 42 项。")).toBeTruthy();
  expect(screen.getAllByText("原文否定发生")).toHaveLength(2);
  expect(screen.queryByText(/前 100 项/)).toBeNull();
});
test("formation dates describe the document and do not certify an event occurred", () => {
  const item: ResearchTimelineItem = {
    id: "document",
    item_kind: "document",
    name: "某函",
    description: "材料记载",
    card_id: "card",
    card_version: 1,
    book_id: "book",
    run_id: "run",
    date_precision: "year",
    status_note: "",
    source_current: true,
    needs_update: false,
    date: { text: "1931年", certainty: "disputed" },
    date_basis: "formation",
    undated: false,
    event_status: "reported",
    evidence_count: 1,
    evidence_ids: ["e"],
  };
  render(
    <QueryClientProvider client={new QueryClient()}>
      <EntityTimeline
        entityId="person"
        onEntity={vi.fn()}
        onCard={vi.fn()}
        onSelectEvent={vi.fn()}
        data={{
          items: [item],
          total: 1,
          dated_total: 1,
          undated_total: 0,
          offset: 0,
          limit: 20,
          truncated: false,
          date_basis: "formation",
        }}
      />
    </QueryClientProvider>,
  );
  expect(screen.getByText("文献形成日期不代表事项发生日期。")).toBeTruthy();
  expect(screen.getByText(/日期有争议/)).toBeTruthy();
  expect(screen.queryByText("材料报告发生")).toBeNull();
});
test("timeline preserves planned events as plans and opens their retained source card", async () => {
  const request = vi.fn(async (_input: RequestInfo | URL) =>
    Response.json({
      items: [
        {
          id: "event",
          name: "拟拨经费",
          kind: "event",
          book_id: "book",
          card_id: "card",
          run_id: "run",
          event_status: "planned",
          status_note: "原文为拟议，未载执行结果。",
          description: "讨论经费方案",
          date: {
            text: "民国二十年",
            certainty: "inferred",
            normalized: "1931",
          },
          evidence: [],
        },
      ],
      truncated: false,
    }),
  );
  vi.stubGlobal("fetch", request);
  const open = vi.fn();
  render(
    <MemoryRouter>
      <QueryClientProvider
        client={
          new QueryClient({ defaultOptions: { queries: { retry: false } } })
        }
      >
        <EntityTimeline entityId="person" onCard={open} onEntity={() => {}} />
      </QueryClientProvider>
    </MemoryRouter>,
  );
  expect(await screen.findByText("计划或拟议事项")).toBeTruthy();
  expect(screen.getByText("原文为拟议，未载执行结果。")).toBeTruthy();
  expect(screen.queryByText("已证实")).toBeNull();
  expect(request.mock.calls[0]?.[0]).toContain("entity_id=person&limit=100");
  await userEvent.click(screen.getByRole("button", { name: "查看事项出处卡" }));
  expect(open).toHaveBeenCalledWith("card");
});
