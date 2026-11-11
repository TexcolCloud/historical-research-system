import { afterEach, expect, test, vi } from "vitest";
import { networkState, networkParams } from "./network-query";
import { research } from "./api";
afterEach(() => vi.unstubAllGlobals());

test("switching views preserves source scope, selection and pagination for a return from original evidence", () => {
  const params = new URLSearchParams(
    "view=network&entity=person&book_id=book&date_from=1948-01&selected=edge-1&selection=relation&timeline_offset=20",
  );
  const next = networkParams(params, { display: "timeline" });
  const state = networkState(next);
  expect(state.entityId).toBe("person");
  expect(state.scope.bookId).toBe("book");
  expect(state.scope.dateFrom).toBe("1948-01");
  expect(state.selection).toEqual({ kind: "relation", id: "edge-1" });
  expect(state.offset).toBe(20);
  expect(state.display).toBe("timeline");
  expect(params.has("display")).toBe(false);
});

test("the compact entity index omits an empty type rather than sending an invalid enum", async () => {
  const fetcher = vi.fn(async (_input: RequestInfo | URL) =>
    Response.json({ items: [], total: 0, offset: 0, limit: 30 }),
  );
  vi.stubGlobal("fetch", fetcher);
  await research.networkEntities("", "", "book", 0);
  const request = new URL(
    String(fetcher.mock.calls[0]?.[0]),
    "http://localhost",
  );
  expect(request.searchParams.has("kind")).toBe(false);
  expect(request.searchParams.getAll("book_ids")).toEqual(["book"]);
});
