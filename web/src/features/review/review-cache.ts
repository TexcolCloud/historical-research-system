import { replaceEqualDeep, type QueryClient } from "@tanstack/react-query";
import type { components } from "@/client/schema.d.ts";
import { queryKeys } from "@/client/query-keys.ts";
import { mergeRunRevision, updateBooks } from "@/features/books/book-cache.ts";
export const issuesKey = queryKeys.issues;

/** A late read cannot revive a completed page or replace a newer evidence revision. */
export function mergeReviewIssue(
  current: components["schemas"]["ReviewIssue"] | undefined,
  incoming: components["schemas"]["ReviewIssue"],
) {
  if (current && current.id === incoming.id) {
    const sameInput =
      current.text_sha256 === incoming.text_sha256 &&
      (current.review_input_sha256 || current.text_sha256) ===
        (incoming.review_input_sha256 || incoming.text_sha256);
    if (
      incoming.revision < current.revision ||
      (incoming.revision === current.revision &&
        (!sameInput ||
          (current.state !== "pending" && incoming.state === "pending")))
    )
      return current;
  }
  return replaceEqualDeep(current, incoming);
}

export async function applyReviewReceipt(
  query: QueryClient,
  receipt: components["schemas"]["ReviewReceipt"],
  submitted?: components["schemas"]["ReviewIssue"],
) {
  const sameInput = () => {
    const current = query.getQueryData<components["schemas"]["ReviewIssue"]>(
      queryKeys.issue(receipt.issue_id),
    );
    return (
      !submitted ||
      !current ||
      (current.text_sha256 === submitted.text_sha256 &&
        (current.review_input_sha256 || current.text_sha256) ===
          (submitted.review_input_sha256 || submitted.text_sha256))
    );
  };
  if (!sameInput()) return false;
  await Promise.all([
    query.cancelQueries({ queryKey: issuesKey(receipt.run_id) }),
    query.cancelQueries({ queryKey: queryKeys.issue(receipt.issue_id) }),
  ]);
  if (!sameInput()) return false;
  query.setQueryData<components["schemas"]["ReviewIssue"]>(
    queryKeys.issue(receipt.issue_id),
    (current) => (current ? { ...current, state: "approved" } : current),
  );
  const patch = {
    state: receipt.state,
    revision: receipt.revision,
    pending_count: receipt.pending_count,
  };
  updateBooks(
    query,
    (book) => book.run_id === receipt.run_id,
    (book) => mergeRunRevision(book, patch),
  );
  query.setQueryData<components["schemas"]["ReviewIssueSummary"][]>(
    issuesKey(receipt.run_id),
    (current) => current?.filter((issue) => issue.id !== receipt.issue_id),
  );
  query.setQueryData<components["schemas"]["RunSummary"]>(
    queryKeys.run(receipt.run_id),
    (current) => (current ? mergeRunRevision(current, patch) : current),
  );
  return true;
}
