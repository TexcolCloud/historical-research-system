import type { QueryClient } from "@tanstack/react-query";
import type { components } from "@/client/schema.d.ts";
import type { Book } from "@/client/api.ts";
import { booksKey } from "@/hooks/events.ts";
export const issuesKey = (run: string) => ["platform", "issues", run] as const;
export function applyReviewReceipt(
  query: QueryClient,
  receipt: components["schemas"]["ReviewReceipt"],
) {
  query.setQueryData<components["schemas"]["ReviewIssueSummary"][]>(
    issuesKey(receipt.run_id),
    (current) => current?.filter((issue) => issue.id !== receipt.issue_id),
  );
  query.setQueryData<Book[]>(booksKey, (current) =>
    current?.map((book) =>
      book.run_id === receipt.run_id
        ? { ...book, state: receipt.state, revision: receipt.revision }
        : book,
    ),
  );
  query.setQueryData<components["schemas"]["RunSummary"]>(
    ["platform", "run", receipt.run_id],
    (current) =>
      current
        ? {
            ...current,
            state: receipt.state,
            revision: receipt.revision,
            pending_count: receipt.pending_count,
          }
        : current,
  );
}
