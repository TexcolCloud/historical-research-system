import { expect, test } from "vitest";
import { QueryClient } from "@tanstack/react-query";
import { applyReviewReceipt, issuesKey } from "./review-cache";

test("a saved decision immediately removes only its issue and preserves the prefetched next page", () => {
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
  applyReviewReceipt(query, {
    decision_id: "decision",
    issue_id: "first",
    run_id: "run",
    revision: 4,
    pending_count: 1,
    next_issue_id: "next",
    state: "awaiting_review",
  });
  expect(query.getQueryData(issuesKey("run"))).toEqual([next]);
  expect(
    query.getQueryState(["platform", "issue", "next"])?.isInvalidated,
  ).toBe(false);
});
