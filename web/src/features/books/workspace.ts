import type { Book } from "@/client/api.ts";
type Responsibility = Pick<Book, "id" | "state"> &
  Partial<
    Pick<
      Book,
      | "pending_count"
      | "navigation_pending_count"
      | "navigation_missing_page_count"
    >
  >;

export function hasNavigationWork(
  book?: Partial<
    Pick<Book, "navigation_pending_count" | "navigation_missing_page_count">
  >,
) {
  return (
    (book?.navigation_pending_count ?? 0) > 0 ||
    (book?.navigation_missing_page_count ?? 0) > 0
  );
}

export function bookWorkspaceHref(book: Responsibility) {
  const tab = bookWorkspaceTab(book);
  return `/books/${book.id}?tab=${tab}${tab === "review" && !book.pending_count && hasNavigationWork(book) ? "&task=navigation" : ""}`;
}

export function bookWorkspaceTab(book: Responsibility) {
  if (
    (book.pending_count ?? 0) > 0 ||
    hasNavigationWork(book) ||
    book.state === "awaiting_review"
  )
    return "review";
  return book.state === "ready" ? "reader" : "progress";
}
