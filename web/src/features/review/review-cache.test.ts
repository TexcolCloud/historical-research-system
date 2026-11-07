import { expect, test } from "vitest";
import { QueryClient } from "@tanstack/react-query";
import {
  applyReviewReceipt,
  issuesKey,
} from "@/features/review/review-cache.ts";

test("a saved decision immediately removes only its issue and preserves the prefetched next page", async () => {
  const query = new QueryClient();
  const next = {
    id: "next",
    run_id: "run",
    page: 12,
    state: "pending",
    revision: 1,
    text_sha256: "b".repeat(64),
  };
  query.setQueryData(issuesKey("run"), [
    { ...next, id: "first", page: 10 },
    next,
  ]);
  query.setQueryData(["platform", "issue", "next"], {
    ...next,
    text: "已预取的正文",
  });
  query.setQueryData(["platform", "book", "book"], {
    id: "book",
    run_id: "run",
    state: "running",
    pending_count: 2,
    navigation_pending_count: 1,
  });
  await applyReviewReceipt(query, {
    decision_id: "decision",
    issue_id: "first",
    run_id: "run",
    revision: 4,
    pending_count: 1,
    next_issue_id: "next",
    state: "awaiting_review",
  });
  expect(query.getQueryData(issuesKey("run"))).toEqual([next]);
  expect(query.getQueryData(["platform", "book", "book"])).toMatchObject({
    pending_count: 1,
    navigation_pending_count: 1,
  });
  expect(
    query.getQueryState(["platform", "issue", "next"])?.isInvalidated,
  ).toBe(false);
});

test("an old decision receipt cannot roll a newer book and run revision back", async () => {
  const query = new QueryClient();
  const current = {
    id: "book",
    run_id: "run",
    state: "ready",
    revision: 9,
    pending_count: 0,
  };
  query.setQueryData(["platform", "books"], [current]);
  query.setQueryData(["platform", "book", "book"], current);
  query.setQueryData(["platform", "run", "run"], { ...current, id: "run" });
  await applyReviewReceipt(query, {
    decision_id: "decision",
    issue_id: "first",
    run_id: "run",
    revision: 4,
    pending_count: 1,
    next_issue_id: null,
    state: "awaiting_review",
  });
  expect(query.getQueryData(["platform", "books"])).toEqual([current]);
  expect(query.getQueryData(["platform", "book", "book"])).toEqual(current);
  expect(query.getQueryData(["platform", "run", "run"])).toMatchObject({
    revision: 9,
    pending_count: 0,
  });
});
