// @vitest-environment happy-dom
import { afterEach, expect, test, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import EntityTimeline from "./EntityTimeline";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
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
