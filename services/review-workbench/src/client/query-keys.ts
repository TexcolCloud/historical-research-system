/** Query identities preserve prefix invalidation used by events and receipts. */
export const queryKeys = {
  books: ["platform", "books"] as const,
  book: (id: string) => ["platform", "book", id] as const,
  run: (id: string) => ["platform", "run", id] as const,
  runs: (book: string) => ["platform", "runs", book] as const,
  issues: (run: string) => ["platform", "issues", run] as const,
  issue: (id: string) => ["platform", "issue", id] as const,
  navigation: (run: string) => ["platform", "navigation", run] as const,
  recovery: (run: string) => ["platform", "recovery", run] as const,
};
