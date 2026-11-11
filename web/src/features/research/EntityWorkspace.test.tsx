// @vitest-environment happy-dom
import { afterEach, expect, test, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import EntityWorkspace from "./EntityWorkspace";
import { respondWith } from "@/test/responses";

vi.mock("./ResearchGraph", () => ({
  default: ({
    onSelectEdge,
  }: {
    onSelectEdge: (edge: { id: string }) => void;
  }) => (
    <button onClick={() => onSelectEdge({ id: "edge" })}>
      选择有出处的关系
    </button>
  ),
}));
vi.mock("./EntityTimeline", () => ({ default: () => <p>时间事项清单</p> }));
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
function Location() {
  return <output aria-label="当前位置">{useLocation().search}</output>;
}

test("selecting a relation reads only its evidence and switching views retains the same source scope", async () => {
  respondWith(() => []);
  const fetcher = vi.fn(async (input: string | URL | Request) => {
    const url = String(input);
    if (url.includes("/network/relations/edge"))
      return Response.json({
        id: "edge",
        source_id: "person",
        target_id: "other",
        predicate: "曾致函",
        reason: "原文明确致函对象",
        card_id: "card",
        card_version: 1,
        book_id: "book",
        evidence: [],
      });
    if (url.includes("/network?"))
      return Response.json({
        entity_id: "person",
        hops: 1,
        limit: 30,
        truncated: false,
        nodes: [{ id: "person", name: "某人物", kind: "person" }],
        edges: [],
      });
    if (url.includes("/timeline?"))
      return Response.json({
        items: [],
        total: 0,
        dated_total: 0,
        undated_total: 0,
        offset: 0,
        limit: 20,
        date_basis: "event",
        truncated: false,
      });
    if (url.includes("/entities/person"))
      return Response.json({
        id: "person",
        name: "某人物",
        kind: "person",
        book_id: "book",
        card_id: "card",
        evidence: [],
      });
    if (url.includes("/network/entities?"))
      return Response.json({ items: [], total: 0, offset: 0, limit: 30 });
    if (url.includes("/books"))
      return Response.json([{ id: "book", title: "所选史料", state: "ready" }]);
    throw new Error(`Unexpected request: ${url}`);
  });
  vi.stubGlobal("fetch", fetcher);
  render(
    <MemoryRouter
      initialEntries={[
        "/research?view=network&entity=person&book_id=book&date_from=1948-01",
      ]}
    >
      <QueryClientProvider
        client={
          new QueryClient({ defaultOptions: { queries: { retry: false } } })
        }
      >
        <EntityWorkspace />
        <Location />
      </QueryClientProvider>
    </MemoryRouter>,
  );
  await userEvent.click(
    await screen.findByRole("button", { name: "选择有出处的关系" }),
  );
  expect(await screen.findByText("原文明确致函对象")).toBeTruthy();
  await userEvent.click(screen.getByRole("tab", { name: "时间线" }));
  const search = screen.getByLabelText("当前位置").textContent || "";
  expect(search).toContain("book_id=book");
  expect(search).toContain("selected=edge");
  expect(search).toContain("date_from=1948-01");
  expect(search).toContain("display=timeline");
  expect(
    fetcher.mock.calls.filter(([url]) =>
      String(url).includes("/network/relations/edge"),
    ),
  ).toHaveLength(1);
  expect(
    fetcher.mock.calls.filter(([url]) =>
      String(url).includes("/entities/other"),
    ),
  ).toHaveLength(0);
});
