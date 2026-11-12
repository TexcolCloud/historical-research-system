import { useQuery } from "@tanstack/react-query";
import { readBooks } from "@/client/api";
import { booksKey } from "@/hooks/events";
import { hasNavigationWork } from "@/features/books/workspace";
import { research, researchKeys } from "@/features/research/api";

/** Sidebar and home share the existing live book and research queues. */
export function useWorkOverview({ enabled = true, loadResearch = true } = {}) {
  const books = useQuery({
    queryKey: booksKey,
    queryFn: ({ signal }) => readBooks(signal),
    staleTime: 30_000,
    retry: false,
    enabled,
  });
  const researchPending = useQuery({
    queryKey: [...researchKeys.pending, "", 0],
    queryFn: ({ signal }) => research.pending("", 0, signal),
    staleTime: 10_000,
    retry: false,
    enabled: enabled && loadResearch,
  });
  const pending =
    books.data?.filter(
      (book) =>
        ["awaiting_review", "failed"].includes(book.state) ||
        (book.pending_count ?? 0) > 0 ||
        hasNavigationWork(book),
    ) ?? [];
  const processing =
    books.data?.filter((book) => ["queued", "running"].includes(book.state)) ??
    [];
  const truncated =
    !!researchPending.data &&
    "truncated" in researchPending.data &&
    researchPending.data.truncated === true;
  return {
    books,
    researchPending,
    pending,
    processing,
    pendingBookCount: books.isSuccess ? pending.length : undefined,
    pendingResearchCount: researchPending.isSuccess
      ? researchPending.data.total
      : undefined,
    truncated,
  };
}
