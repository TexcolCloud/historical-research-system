import type { QueryClient } from "@tanstack/react-query";
import type { components } from "@/client/schema.d.ts";
import { queryKeys } from "@/client/query-keys.ts";
import { mergeRunRevision, updateBooks } from "@/features/books/book-cache.ts";
export const issuesKey = queryKeys.issues;
export function applyReviewReceipt(
  query: QueryClient,
  receipt: components["schemas"]["ReviewReceipt"],
) {
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
}
