import { expect, test } from "vitest";
import { QueryClient } from "@tanstack/react-query";
import { applyBookEvent, booksKey } from "./events.ts";

test("page telemetry refreshes only run progress without invalidating reading or review content", () => {
  const query = new QueryClient();
  for (const key of [
    booksKey,
    ["platform", "runs", "book"],
    ["platform", "issues", "run"],
    ["platform", "chapters", "book"],
  ]) {
    query.setQueryData(key, []);
  }
  applyBookEvent(
    query,
    JSON.stringify({
      sequence: 11,
      book_id: "book",
      run_id: "run",
      kind: "run.progress",
      payload: { progress: { completed: 12, total: 481 } },
    }),
  );
  expect(query.getQueryState(["platform", "runs", "book"])?.isInvalidated).toBe(
    true,
  );
  for (const key of [
    booksKey,
    ["platform", "issues", "run"],
    ["platform", "chapters", "book"],
  ]) {
    expect(query.getQueryState(key)?.isInvalidated).toBe(false);
  }
});

test("progress patches only its book without dropping the cached list", () => {
  const query = new QueryClient();
  const other = { id: "other", title: "另一册", state: "processing" };
  query.setQueryData(booksKey, [
    { id: "book", title: "原书", state: "processing" },
    other,
  ]);
  applyBookEvent(
    query,
    JSON.stringify({
      sequence: 3,
      book_id: "book",
      run_id: "run",
      kind: "run.changed",
      payload: { state: "awaiting_review", stage: "review" },
    }),
  );
  expect(query.getQueryData(booksKey)).toEqual([
    {
      id: "book",
      title: "原书",
      state: "awaiting_review",
      stage: "review",
      run_id: "run",
    },
    other,
  ]);
  expect(applyBookEvent(query, "invalid")).toBeNull();
});

test("card execution updates never replace the parent book ingestion state", () => {
  const query = new QueryClient();
  const book = {
    id: "book",
    run_id: "book-run",
    state: "ready",
    title: "原书",
  };
  query.setQueryData(booksKey, [book]);
  applyBookEvent(
    query,
    JSON.stringify({
      sequence: 8,
      book_id: "book",
      run_id: "card-run",
      kind: "run.changed",
      payload: { state: "failed", stage: "vision", run_kind: "cards" },
    }),
  );
  expect(query.getQueryData(booksKey)).toEqual([book]);
});

test("replayed older progress cannot regress a freshly loaded book", () => {
  const query = new QueryClient();
  const book = {
    id: "book",
    run_id: "run",
    state: "ready",
    revision: 9,
    title: "原书",
  };
  query.setQueryData(booksKey, [book]);
  applyBookEvent(
    query,
    JSON.stringify({
      sequence: 2,
      book_id: "book",
      run_id: "run",
      kind: "run.changed",
      payload: { state: "processing", revision: 3 },
    }),
  );
  expect(query.getQueryData(booksKey)).toEqual([book]);
});

test("card failure refreshes its run and execution graph despite a higher book revision", () => {
  const query = new QueryClient();
  const book = {
    id: "book",
    run_id: "book-run",
    state: "ready",
    revision: 300,
  };
  query.setQueryData(booksKey, [book]);
  query.setQueryData(["platform", "book", "book"], book);
  const keys = [
    ["platform", "runs", "book"],
    ["platform", "executions", "card-run"],
  ];
  keys.forEach((key) => query.setQueryData(key, []));
  applyBookEvent(
    query,
    JSON.stringify({
      sequence: 301,
      book_id: "book",
      run_id: "card-run",
      kind: "run.changed",
      payload: {
        state: "failed",
        stage: "planning",
        run_kind: "cards",
        revision: 2,
      },
    }),
  );
  keys.forEach((key) =>
    expect(query.getQueryState(key)?.isInvalidated).toBe(true),
  );
  expect(query.getQueryData(booksKey)).toEqual([book]);
  expect(query.getQueryData(["platform", "book", "book"])).toEqual(book);
});
