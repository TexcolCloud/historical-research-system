import type { Book } from "@/client/api.ts";
type Responsibility = Pick<Book, "id" | "state"> &
  Partial<Pick<Book, "pending_count" | "navigation_pending_count">>;

export function bookWorkspaceHref(book: Responsibility) {
  const tab = bookWorkspaceTab(book);
  return `/books/${book.id}?tab=${tab}${tab === "review" && !book.pending_count && (book.navigation_pending_count ?? 0) > 0 ? "&task=navigation" : ""}`;
}

export function bookWorkspaceTab(book: Responsibility) {
  if (
    (book.pending_count ?? 0) > 0 ||
    (book.navigation_pending_count ?? 0) > 0 ||
    book.state === "awaiting_review"
  )
    return "review";
  return book.state === "ready" ? "reader" : "progress";
}
