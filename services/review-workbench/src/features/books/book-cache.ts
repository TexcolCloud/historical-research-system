import type { QueryClient } from "@tanstack/react-query";
import type { Book } from "@/client/api.ts";
import { queryKeys } from "@/client/query-keys.ts";

/** Update both views of an existing book, with the caller's identity policy. */
export function updateBooks(
  query: QueryClient,
  matches: (book: Book) => boolean,
  update: (book: Book) => Book,
) {
  for (const [key, book] of query.getQueriesData<Book>({
    queryKey: ["platform", "book"],
  })) {
    if (book && matches(book)) query.setQueryData(key, update(book));
  }
  query.setQueryData<Book[]>(queryKeys.books, (books) =>
    books?.map((book) => (matches(book) ? update(book) : book)),
  );
}

/** Only revisions of the same run can be compared. */
export function mergeRunRevision<T extends { revision?: number }>(
  current: T,
  patch: Partial<T> & { revision: number },
): T {
  return current.revision !== undefined && current.revision > patch.revision
    ? current
    : { ...current, ...patch };
}
