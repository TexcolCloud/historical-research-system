// @vitest-environment happy-dom
import { afterEach, expect, test, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import ResearchPendingPanel from "./ResearchPending";
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
test("research identity and card disputes are actionable in one inbox without resurfacing superseded records", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      Response.json({
        items: [
          {
            id: "i",
            kind: "identity",
            title: "同名人物身份待核",
            entity_id: "person",
          },
          {
            id: "c",
            kind: "card",
            title: "最新卡片需要核验",
            card_id: "current",
          },
          {
            id: "old",
            kind: "card",
            title: "已经替代的旧疑点",
            card_id: "old",
            superseded: true,
          },
        ],
        total: 2,
        offset: 0,
        limit: 50,
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
        <ResearchPendingPanel />
      </QueryClientProvider>
    </MemoryRouter>,
  );
  expect(
    (
      await screen.findByRole("link", { name: "同名人物身份待核" })
    ).getAttribute("href"),
  ).toContain("entity=person");
  expect(
    screen.getByRole("link", { name: "最新卡片需要核验" }).getAttribute("href"),
  ).toContain("card=current");
  expect(screen.queryByText("已经替代的旧疑点")).toBeNull();
});
